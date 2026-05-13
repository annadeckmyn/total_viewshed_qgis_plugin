"""Worker thread for viewshed computation."""

from __future__ import annotations

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

        center_elev = tile_dem[row_indices, col_indices].astype(xp.float32, copy=False)
        center_elev = xp.where(xp.isfinite(center_elev), center_elev, xp.nan)

        visible = xp.zeros(row_indices.shape[0], dtype=xp.float32)

        for dir_start in range(0, directions.size, self.RAY_BATCH_SIZE):
            dir_batch = directions[dir_start:dir_start + self.RAY_BATCH_SIZE]
            sin_batch = xp.sin(dir_batch)[:, None]
            cos_batch = xp.cos(dir_batch)[:, None]

            row_offsets = xp.rint(steps[None, :] * sin_batch).astype(xp.int32)
            col_offsets = xp.rint(steps[None, :] * cos_batch).astype(xp.int32)

            for cell_start in range(0, row_indices.shape[0], self.CELL_BATCH_SIZE):
                cell_end = min(cell_start + self.CELL_BATCH_SIZE, row_indices.shape[0])

                batch_rows = row_indices[cell_start:cell_end][:, None, None]
                batch_cols = col_indices[cell_start:cell_end][:, None, None]
                batch_center = center_elev[cell_start:cell_end][:, None, None] + observer_height

                sample_rows = batch_rows - row_offsets[None, :, :]
                sample_cols = batch_cols + col_offsets[None, :, :]
                sample_elev = tile_dem[sample_rows, sample_cols].astype(xp.float32, copy=False)
                sample_elev = xp.where(xp.isfinite(sample_elev), sample_elev, -xp.inf)

                angles = xp.arctan2(sample_elev - batch_center, steps[None, None, :] * cell_size)
                prior_max = xp.full(angles.shape[:-1], -xp.inf, dtype=angles.dtype)
                visible_count = xp.zeros(angles.shape[:-1], dtype=xp.float32)
                for step_idx in range(angles.shape[-1]):
                    current_angle = angles[..., step_idx]
                    visible_count += current_angle > prior_max
                    prior_max = xp.maximum(prior_max, current_angle)

                visible[cell_start:cell_end] += xp.sum(visible_count, axis=1)

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

                    tile = src.read(1, window=padded_window, boundless=True, masked=True)
                    tile_dem = tile.filled(np.nan).astype(np.float32, copy=False)
                    if xp is not np:
                        tile_dem = xp.asarray(tile_dem)

                    core_row_start = pad
                    core_col_start = pad
                    core_h = int(core_window.height)
                    core_w = int(core_window.width)

                    if chm is not None:
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
                        padded_tform = rasterio.windows.transform(padded_window, transform=transform)
                        padded_shape = (int(padded_window.height), int(padded_window.width))
                        cut_mask_padded = geometry_mask(
                            cutline_shapes,
                            out_shape=padded_shape,
                            transform=padded_tform,
                            invert=True,
                        )
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
            with rasterio.open(self.output_path, 'w', **profile) as dst:
                dst.write(result, 1)

            elapsed_time = perf_counter() - start_time
            self.log.emit(f'Computation completed in {elapsed_time:.2f}s ({elapsed_time / 3600:.4f} hours)')

        finally:
            if chm is not None:
                chm.close()
