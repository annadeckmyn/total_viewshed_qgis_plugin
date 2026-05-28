"""Worker thread for viewshed computation."""

from __future__ import annotations

import math
from math import ceil
from time import perf_counter

import json
from osgeo import ogr
import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from qgis.PyQt.QtCore import QObject, pyqtSignal

try:
    import cupy as cp
    CUPY_IMPORT_ERROR = None
except Exception as exc:
    cp = None
    CUPY_IMPORT_ERROR = exc

try:
    from numba import njit, prange
    NUMBA_AVAILABLE = True
except Exception:
    NUMBA_AVAILABLE = False

if NUMBA_AVAILABLE:
    @njit(parallel=True)
    def _numba_viewshed_batch(tile_dem, base_rows, base_cols, centers, directions, steps, cell_size, step_chunk):
        n_cells = base_rows.shape[0]
        n_dirs = directions.shape[0]
        n_steps = steps.shape[0]
        nrows = tile_dem.shape[0]
        ncols = tile_dem.shape[1]
        out = np.zeros(n_cells, dtype=np.float32)

        for i in prange(n_cells):
            r = base_rows[i]
            c = base_cols[i]
            cen = centers[i]
            count = 0.0
            for d in range(n_dirs):
                theta = directions[d]
                s = math.sin(theta)
                co = math.cos(theta)
                prior_max = -1e38
                # iterate steps (chunking reduces memory in the vectorized version,
                # here we simply iterate but keep the same stepping semantics)
                for si in range(n_steps):
                    off_r = int(round(steps[si] * s))
                    off_c = int(round(steps[si] * co))
                    sr = r - off_r
                    sc = c + off_c
                    if sr < 0 or sc < 0 or sr >= nrows or sc >= ncols:
                        elev = -1e38
                    else:
                        elev = tile_dem[sr, sc]
                        if np.isnan(elev):
                            elev = -1e38
                    angle = math.atan2(elev - cen, steps[si] * cell_size)
                    if angle > prior_max:
                        count += 1.0
                        prior_max = angle
            out[i] = count
        return out


NODATA = -9999


class ViewshedWorker(QObject):
    """Worker thread for computing viewshed."""

    finished = pyqtSignal(str)  # Output file path
    error = pyqtSignal(str)  # Error message
    progress = pyqtSignal(int)  # Progress percentage
    log = pyqtSignal(str)  # Log message

    # Computation parameters (set as class attributes for easy tuning)
    RAY_BATCH_SIZE = 32
    CELL_BATCH_SIZE = 256

    def __init__(self, dem_path, chm_path=None, output_path=None,
                 n_dirs=90, observer_height=1.6, max_radius_meters=1200,
                 tile_size=256, backend='auto', convert_to_hectares=True,
                 cutline_path=None):
        """Initialize worker.

        Args:
            dem_path: Path to DEM raster
            chm_path: Path to CHM raster (optional)
            output_path: Path for output viewshed
            n_dirs: Number of ray directions
            observer_height: Observer height in meters
            max_radius_meters: Maximum search radius in meters
            tile_size: Tile size for processing
            backend: 'numpy', 'cupy', or 'auto'
            convert_to_hectares: Whether to convert result to hectares
        """
        super().__init__()
        self.dem_path = dem_path
        self.chm_path = chm_path
        self.output_path = output_path
        self.n_dirs = n_dirs
        self.observer_height = observer_height
        self.max_radius_meters = max_radius_meters
        self.tile_size = tile_size
        self.backend = backend
        self.convert_to_hectares = convert_to_hectares
        self.cutline_path = cutline_path
        self.stop_flag = False
        # Lightweight profiling counters (seconds)
        self._prof = {
            'viewshed_batch_time': 0.0,
            'tile_io_time': 0.0,
            'chm_time': 0.0,
            'cutline_time': 0.0,
            'tile_processing_time': 0.0,
            'write_time': 0.0,
        }

    def stop(self):
        """Request computation stop."""
        self.stop_flag = True

    def run(self):
        """Execute the viewshed computation."""
        try:
            self.compute_viewshed()
            self.finished.emit(self.output_path)
        except Exception as e:
            self.error.emit(str(e))

    def get_xp(self, backend: str):
        """Get numpy or cupy backend."""
        if backend == 'cupy':
            if cp is None:
                raise RuntimeError(
                    'CuPy backend requested, but CuPy is not available.'
                ) from CUPY_IMPORT_ERROR
            return cp
        if backend == 'auto' and cp is not None:
            self.log.emit('CuPy available: using GPU backend')
            return cp
        self.log.emit('Using NumPy backend (CPU)')
        return np

    def generate_directions(self, n_dirs, xp):
        """Generate ray directions."""
        return xp.linspace(0.0, 2.0 * xp.pi, n_dirs, endpoint=False)

    def iter_tile_windows(self, width, height, tile_size):
        """Iterate over tile windows."""
        for row_off in range(0, height, tile_size):
            for col_off in range(0, width, tile_size):
                win_height = min(tile_size, height - row_off)
                win_width = min(tile_size, width - col_off)
                yield Window(col_off, row_off, win_width, win_height)

    def tile_radius_cells(self, max_radius_meters, cell_size, raster_width, raster_height):
        """Calculate radius in cells."""
        if max_radius_meters is None:
            return max(raster_width, raster_height)
        return max(1, int(ceil(max_radius_meters / cell_size)))

    def viewshed_batch(self, tile_dem, row_indices, col_indices, directions, steps,
                       observer_height, cell_size, xp):
        """Compute viewshed for a batch of cells."""
        if row_indices.size == 0:
            return xp.zeros((0,), dtype=xp.float32)

        t_batch_begin = perf_counter()

        center_elev = tile_dem[row_indices, col_indices].astype(xp.float32, copy=False)
        center_elev = xp.where(xp.isfinite(center_elev), center_elev, xp.nan)

        visible = xp.zeros(row_indices.shape[0], dtype=xp.float32)

        # Fast path: if using NumPy backend and Numba is available, use the JIT inner loop
        if xp is np and NUMBA_AVAILABLE:
            try:
                # Ensure numpy arrays and dtypes
                tile_dem_np = tile_dem if isinstance(tile_dem, np.ndarray) else np.asarray(tile_dem)
                rows_np = row_indices.astype(np.int32, copy=False) if row_indices.dtype != np.int32 else row_indices
                cols_np = col_indices.astype(np.int32, copy=False) if col_indices.dtype != np.int32 else col_indices
                dirs_np = directions if isinstance(directions, np.ndarray) else np.asarray(directions)
                steps_np = steps if isinstance(steps, np.ndarray) else np.asarray(steps)

                step_chunk = max(64, min(256, int(steps_np.size)))
                n_cells = rows_np.shape[0]

                for cell_start in range(0, n_cells, self.CELL_BATCH_SIZE):
                    cell_end = min(cell_start + self.CELL_BATCH_SIZE, n_cells)
                    base_rows = rows_np[cell_start:cell_end]
                    base_cols = cols_np[cell_start:cell_end]
                    centers = (center_elev[cell_start:cell_end] + observer_height).astype(np.float64)

                    res_batch = _numba_viewshed_batch(tile_dem_np, base_rows, base_cols, centers, dirs_np, steps_np, float(cell_size), int(step_chunk))
                    visible[cell_start:cell_end] = res_batch.astype(np.float32)

                elapsed = perf_counter() - t_batch_begin
                try:
                    self._prof['viewshed_batch_time'] += float(elapsed)
                except Exception:
                    pass

                return visible
            except Exception as e:
                # If numba path fails, fall back to vectorized implementation
                self.log.emit(f'Numba fast-path failed, falling back to vectorized path: {e}')

        # Process directions one at a time to avoid creating large 3D arrays
        # and chunk steps to bound memory usage for large radii.
        STEP_CHUNK = max(64, min(256, int(steps.size)))
        n_cells = row_indices.shape[0]
        for dir_start in range(0, directions.size, self.RAY_BATCH_SIZE):
            dir_batch = directions[dir_start:dir_start + self.RAY_BATCH_SIZE]

            for cell_start in range(0, n_cells, self.CELL_BATCH_SIZE):
                cell_end = min(cell_start + self.CELL_BATCH_SIZE, n_cells)

                base_rows = row_indices[cell_start:cell_end]
                base_cols = col_indices[cell_start:cell_end]
                centers = center_elev[cell_start:cell_end] + observer_height

                # accumulate counts per cell across directions
                cell_counts = xp.zeros((cell_end - cell_start,), dtype=xp.float32)

                for d in range(dir_batch.size):
                    theta = dir_batch[d]
                    s = xp.sin(theta)
                    c = xp.cos(theta)

                    row_offsets_1d = xp.rint(steps * s).astype(xp.int32)
                    col_offsets_1d = xp.rint(steps * c).astype(xp.int32)

                    # running prior max per cell for this direction
                    prior_max = xp.full((cell_end - cell_start,), -xp.inf, dtype=xp.float32)

                    # process steps in chunks to limit memory
                    for s_start in range(0, steps.size, STEP_CHUNK):
                        s_end = min(s_start + STEP_CHUNK, steps.size)
                        step_chunk = steps[s_start:s_end]
                        ro_chunk = row_offsets_1d[s_start:s_end]
                        co_chunk = col_offsets_1d[s_start:s_end]

                        # build sample index arrays (cells x chunk)
                        sample_rows = (base_rows[:, None] - ro_chunk[None, :]).astype(xp.int32)
                        sample_cols = (base_cols[:, None] + co_chunk[None, :]).astype(xp.int32)

                        # valid mask for indices inside tile_dem
                        nrows = int(tile_dem.shape[0])
                        ncols = int(tile_dem.shape[1])
                        valid = (sample_rows >= 0) & (sample_rows < nrows) & (sample_cols >= 0) & (sample_cols < ncols)

                        # clip for safe indexing, then mask out invalid locations
                        sample_rows_clipped = xp.clip(sample_rows, 0, nrows - 1)
                        sample_cols_clipped = xp.clip(sample_cols, 0, ncols - 1)

                        sample_elev = tile_dem[sample_rows_clipped, sample_cols_clipped].astype(xp.float32, copy=False)
                        sample_elev = xp.where(valid, sample_elev, -xp.inf)

                        centers_exp = centers[:, None]
                        angles = xp.arctan2(sample_elev - centers_exp, step_chunk[None, :] * cell_size)

                        # running max within the chunk
                        running_max = xp.maximum.accumulate(angles, axis=-1)
                        prior_shift = xp.empty_like(running_max)
                        prior_shift[:, 0] = -xp.inf
                        prior_shift[:, 1:] = running_max[:, :-1]

                        visible_chunk = xp.sum(angles > prior_shift, axis=-1).astype(xp.float32)

                        # update prior_max for next chunk: take max of previous prior and last running_max
                        prior_max = xp.maximum(prior_max, running_max[:, -1])

                        cell_counts += visible_chunk

                visible[cell_start:cell_end] += cell_counts

        elapsed = perf_counter() - t_batch_begin
        try:
            self._prof['viewshed_batch_time'] += float(elapsed)
        except Exception:
            pass

        return visible

    def compute_viewshed(self):
        """Main viewshed computation."""
        xp = self.get_xp(self.backend)
        start_time = perf_counter()

        self.log.emit(f'Loading DEM: {self.dem_path}')

        with rasterio.open(self.dem_path) as src:
            transform = src.transform
            profile = src.profile.copy()
            profile.update(
                dtype=rasterio.float32,
                count=1,
                compress='lzw',
                tiled=True,
                blockxsize=256,
                blockysize=256,
                bigtiff='yes',
            )
            height, width = src.shape
            cell_size = float(abs(transform.a))
            self.log.emit(f'DEM shape: {height}x{width}, cell size: {cell_size}m')

            # Load cutline shapes (optional) and compute a crop window
            cutline_shapes = None
            cutline_window = None
            crop_row_off = 0
            crop_col_off = 0
            if self.cutline_path:
                try:
                    ds = ogr.Open(self.cutline_path)
                    if ds is None:
                        raise RuntimeError(f'OGR failed to open {self.cutline_path}')
                    layer = ds.GetLayer()
                    shapes = []
                    minx = float('inf')
                    miny = float('inf')
                    maxx = float('-inf')
                    maxy = float('-inf')
                    for feat in layer:
                        geom = feat.GetGeometryRef()
                        if geom is None:
                            continue
                        env = geom.GetEnvelope()  # (minX, maxX, minY, maxY)
                        minx = min(minx, env[0])
                        maxx = max(maxx, env[1])
                        miny = min(miny, env[2])
                        maxy = max(maxy, env[3])
                        shapes.append(json.loads(geom.ExportToJson()))
                    ds = None
                    if shapes:
                        cutline_shapes = shapes
                        bounds = (minx, miny, maxx, maxy)
                        cutline_window = rasterio.windows.from_bounds(*bounds, transform=transform)

                        # Crop the working extent to the cutline window
                        crop_row_off = int(cutline_window.row_off)
                        crop_col_off = int(cutline_window.col_off)
                        height = int(cutline_window.height)
                        width = int(cutline_window.width)

                        # Update output profile for cropped extent
                        cropped_transform = rasterio.windows.transform(cutline_window, transform)
                        profile.update(
                            width=int(cutline_window.width),
                            height=int(cutline_window.height),
                            transform=cropped_transform,
                        )
                        profile.update(nodata=NODATA)
                        self.log.emit(f'Cutline provided; cropping to window {cutline_window}')
                except Exception as e:
                    self.log.emit(f'Warning: failed to load cutline {self.cutline_path}: {e}')

        chm = None
        if self.chm_path:
            self.log.emit(f'Loading CHM: {self.chm_path}')
            chm = rasterio.open(self.chm_path)
            self.log.emit(f'CHM loaded: {chm.shape}')

        try:
            radius_cells = self.tile_radius_cells(
                self.max_radius_meters, cell_size, width, height
            )
            self.log.emit(f'Search radius: {radius_cells} cells')

            directions = self.generate_directions(self.n_dirs, xp)
            steps = xp.arange(1, radius_cells + 1, dtype=xp.float32)

            result = np.full((height, width), NODATA, dtype=np.float32)

            # Progress tracking
            n_cols = int(ceil(width / self.tile_size))
            n_rows = int(ceil(height / self.tile_size))
            total_tiles = max(1, n_cols * n_rows)
            processed_tiles = 0

            self.log.emit(f'Processing {total_tiles} tiles...')

            with rasterio.open(self.dem_path) as src:
                for core_window in self.iter_tile_windows(width, height, self.tile_size):
                    if self.stop_flag:
                        self.log.emit('Computation cancelled.')
                        return

                    pad = radius_cells
                    # Local (cropped) indices for result array
                    row_off = int(core_window.row_off)
                    col_off = int(core_window.col_off)
                    row_end = row_off + int(core_window.height)
                    col_end = col_off + int(core_window.width)

                    # Map core window to global DEM coords when cropped
                    if cutline_window is not None:
                        global_row_off = row_off + crop_row_off
                        global_col_off = col_off + crop_col_off
                    else:
                        global_row_off = row_off
                        global_col_off = col_off

                    padded_window = Window(
                        global_col_off - pad,
                        global_row_off - pad,
                        int(core_window.width) + 2 * pad,
                        int(core_window.height) + 2 * pad,
                    )

                    t_tile_io = perf_counter()
                    tile = src.read(1, window=padded_window, boundless=True, masked=True)
                    t_tile_io = perf_counter() - t_tile_io
                    self._prof['tile_io_time'] += float(t_tile_io)
                    tile_dem = tile.filled(np.nan).astype(np.float32, copy=False)
                    if xp is not np:
                        tile_dem = xp.asarray(tile_dem)

                    core_row_start = pad
                    core_col_start = pad
                    core_h = int(core_window.height)
                    core_w = int(core_window.width)

                    if chm is not None:
                        t_chm = perf_counter()
                        padded_bounds = rasterio.windows.bounds(padded_window, transform=transform)
                        chm_window = rasterio.windows.from_bounds(
                            *padded_bounds, transform=chm.transform
                        )
                        out_rows = int(padded_window.height)
                        out_cols = int(padded_window.width)
                        chm_tile_padded = chm.read(
                            1,
                            window=chm_window,
                            out_shape=(out_rows, out_cols),
                            resampling=Resampling.nearest,
                            boundless=True,
                            masked=True,
                        )
                        t_chm = perf_counter() - t_chm
                        self._prof['chm_time'] += float(t_chm)
                        chm_tile_core = chm_tile_padded[
                            core_row_start:core_row_start + core_h,
                            core_col_start:core_col_start + core_w
                        ]
                        chm_mask = np.ma.getmaskarray(chm_tile_core)
                        chm_data = chm_tile_core.filled(0).astype(np.float32, copy=False)
                        compute_mask = chm_mask | np.isclose(chm_data, 0.0)
                        chm_positive = (~chm_mask) & (chm_data > 0)
                    else:
                        compute_mask = np.ones((core_h, core_w), dtype=bool)
                        chm_positive = np.zeros((core_h, core_w), dtype=bool)

                    # Rasterize cutline for this padded window (or treat as all True)
                    if cutline_shapes is not None:
                        t_cut = perf_counter()
                        padded_tform = rasterio.windows.transform(padded_window, transform=transform)
                        padded_shape = (int(padded_window.height), int(padded_window.width))
                        cut_mask_padded = geometry_mask(
                            cutline_shapes,
                            out_shape=padded_shape,
                            transform=padded_tform,
                            invert=True,
                        )
                        t_cut = perf_counter() - t_cut
                        self._prof['cutline_time'] += float(t_cut)
                        cut_mask_core = cut_mask_padded[
                            core_row_start:core_row_start + core_h,
                            core_col_start:core_col_start + core_w,
                        ]
                    else:
                        cut_mask_core = np.ones((core_h, core_w), dtype=bool)

                    # Only compute inside cutline
                    compute_mask = compute_mask & cut_mask_core

                    # Prepare core_result: default nodata, set CHM>0 cells inside cutline to 0
                    core_result = np.full((core_h, core_w), NODATA, dtype=np.float32)
                    if np.any(cut_mask_core & chm_positive):
                        core_result[cut_mask_core & chm_positive] = 0.0

                    if not np.any(compute_mask):
                        processed_tiles += 1
                        percent = int(processed_tiles / total_tiles * 100)
                        self.progress.emit(percent)
                        continue

                    row_coords, col_coords = np.indices((core_h, core_w))
                    flat_rows = row_coords[compute_mask] + core_row_start
                    flat_cols = col_coords[compute_mask] + core_col_start

                    t_tile_proc = perf_counter()
                    batch_visibility = self.viewshed_batch(
                        tile_dem,
                        xp.asarray(flat_rows) if xp is not np else flat_rows,
                        xp.asarray(flat_cols) if xp is not np else flat_cols,
                        directions,
                        steps,
                        self.observer_height,
                        cell_size,
                        xp,
                    )
                    t_tile_proc = perf_counter() - t_tile_proc
                    self._prof['tile_processing_time'] += float(t_tile_proc)

                    if xp is not np:
                        batch_visibility = xp.asnumpy(batch_visibility)

                    core_result[compute_mask] = batch_visibility
                    result[row_off:row_end, col_off:col_end] = core_result

                    processed_tiles += 1
                    percent = int(processed_tiles / total_tiles * 100)
                    self.progress.emit(percent)

            if self.convert_to_hectares:
                cell_area = float(cell_size) * float(cell_size)
                valid = result != NODATA
                result[valid] = result[valid] * cell_area / 10000

            # Write output
            self.log.emit(f'Writing output to: {self.output_path}')
            t_write = perf_counter()
            with rasterio.open(self.output_path, 'w', **profile) as dst:
                dst.write(result, 1)
            t_write = perf_counter() - t_write
            self._prof['write_time'] += float(t_write)

            elapsed_time = perf_counter() - start_time
            self.log.emit(f'Computation completed in {elapsed_time:.2f}s ({elapsed_time / 3600:.4f} hours)')
            # Emit profiling summary
            try:
                vb = self._prof.get('viewshed_batch_time', 0.0)
                tio = self._prof.get('tile_io_time', 0.0)
                chm_t = self._prof.get('chm_time', 0.0)
                cut_t = self._prof.get('cutline_time', 0.0)
                tproc = self._prof.get('tile_processing_time', 0.0)
                wtime = self._prof.get('write_time', 0.0)
                self.log.emit(f'Profiling summary (s): viewshed_batch={vb:.3f}, tile_io={tio:.3f}, chm={chm_t:.3f}, cutline={cut_t:.3f}, tile_proc={tproc:.3f}, write={wtime:.3f}')
            except Exception:
                pass

        finally:
            if chm is not None:
                chm.close()