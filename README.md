# Total Viewshed Calculator QGIS Plugin

A QGIS plugin for computing total (cumulative) viewshed analysis from Digital Elevation Models (DEMs). This plugin efficiently processes large rasters using tiled computation and supports optional GPU acceleration via CuPy.

## Features

- **Efficient Tiled Processing**: Processes large DEMs in tiles to manage memory efficiently
- **GPU Acceleration**: Optional CuPy backend for GPU-accelerated computation on compatible hardware
- **Optional CHM Masking**: Use a Canopy Height Model to mask certain areas from computation
- **Vector Cutline Support**: Optionally restrict computation to a vector cutline (select a layer or provide a file). The plugin uses GDAL/OGR (`osgeo`) for cutline geometries; Fiona is not required.
- **Flexible Parameters**:
  - Ray directions: Control accuracy vs. speed trade-off
  - Observer height: Customize eye level height above terrain
  - Maximum radius: Limit search distance
  - Tile size: Optimize for your system's memory
- **Hectare Conversion**: Optionally convert output to hectares
- **Live Progress Tracking**: Monitor computation progress in real-time
- **Canvas Integration**: Automatically load results to the QGIS map canvas

## Installation

### Prerequisites

Ensure you have QGIS 3.20 or later installed. This plugin requires:
- PyQGIS (included with QGIS)
- NumPy
- Rasterio
 - GDAL/OGR (`osgeo`) — available in QGIS / OSGeo4W

### Installation Steps

1. **Copy the plugin to your QGIS plugins directory:**
   
   Find your QGIS plugins directory:
   - **Windows**: `C:\Users\<USERNAME>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins`
   - **Linux**: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins`
   - **macOS**: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins`

   Copy the entire `total_viewshed_qgis_plugin` folder to this directory.

2. **(Optional) Install CuPy for GPU acceleration:**
   
   If you have a compatible NVIDIA GPU, install CuPy in your QGIS Python environment:
   
   ```bash
   pip install cupy-cuda11x  # Replace 11x with your CUDA version (11.2, 11.8, 12.x, etc.)
   ```

3. **Enable the plugin in QGIS:**
   
   - Open QGIS
   - Go to: **Plugins → Manage and Install Plugins**
   - Search for "Total Viewshed"
   - Check the box to enable it
   - Click **Close**

### Installation on QGIS with Python-QGIS Batch

On Windows with OSGeo4W QGIS installation, you can install packages via the Python-QGIS batch:

```batch
# Launch Python-QGIS console
C:\Program Files\QGIS 3.44.9\bin\python-qgis-ltr.bat

# Inside Python, install packages
pip install cupy-cuda12x rasterio
```

## Usage

1. **Open the plugin:**
   - In QGIS menu: **Total Viewshed → Total Viewshed**
   - Or click the viewshed icon in the toolbar

2. **Configure parameters:**
   - **DEM Layer**: Select the Digital Elevation Model to process
   - **CHM Layer (optional)**: Select a Canopy Height Model to mask areas
   - **Cutline (optional)**: Choose a vector layer containing the cutline to restrict computation, or leave empty to process the full DEM.
   - **Ray Directions**: Number of directions to scan (more = slower but more accurate)
   - **Observer Height**: Height above terrain (typically 1.6m for human eye level)
   - **Max Radius**: Maximum viewing distance in meters (0 = unlimited)
   - **Tile Size**: Larger tiles are faster but use more memory
   - **Backend**: Choose CPU (NumPy) or GPU (CuPy) computation
   - **Convert to Hectares**: Output in hectare units

3. **Set output:**
   - Leave empty to save to a temporary directory
   - Or specify a custom output path ending in `.tif`
   - Check "Load result to canvas" to automatically add the result layer

4. **Run computation:**
   - Click **Compute Viewshed**
   - Monitor progress in the progress bar
   - View detailed log messages

5. **Results:**
   - Output is saved as a GeoTIFF raster
   - Values represent cumulative visible area
   - When converted to hectares, values represent visible area in hectares

## Parameters Guide

### Ray Directions (N_DIRS)
- **Low (4-16)**: Fast but coarse visibility pattern
- **Medium (32-90)**: Good balance (recommended: 90)
- **High (180-360)**: Very accurate but slower

### Observer Height
- **Standard**: 1.6m (human eye level)
- **Taller**: 2.0-2.5m (observer standing on elevated ground)
- **Lower**: 0.0-1.0m (for specific terrain analysis)

### Max Radius
- **0 or unlimited**: Full raster extent (slow for large areas)
- **1000-1200m**: Typical for orchard/landscape analysis
- **Specific needs**: Limit based on study area requirements

### Tile Size
- **Small (64-128)**: Lower memory, slower overall
- **Medium (256)**: Good default (recommended)
- **Large (512+)**: Faster if you have >8GB RAM available

### Backend
- **Auto**: Use GPU if CuPy is available, otherwise CPU
- **NumPy (CPU)**: Always works, slower (good for testing)
- **CuPy (GPU)**: Fast if available, requires compatible NVIDIA GPU

## Performance Tips

1. **For large rasters (>1000x1000 pixels):**
   - Use GPU backend if available
   - Use smaller max radius to reduce computation time
   - Use medium tile size (256-512)

2. **For quick analysis:**
   - Reduce ray directions to 32-45
   - Set reasonable max radius
   - Use GPU if available

3. **For high accuracy:**
   - Increase ray directions to 180-360
   - Process with larger radius
   - Accept longer computation times

## Output

The plugin generates a GeoTIFF raster where:
- **Cell value**: Cumulative visible area from all points in that cell's viewshed
- **Units**: Square meters (or hectares if conversion enabled)
- **Data type**: 32-bit float
- **Compression**: LZW (lossless)

## Troubleshooting

### Plugin doesn't appear in QGIS menu
- Verify the plugin folder is in the correct plugins directory
- Restart QGIS
- Check the Python console for error messages

### "CuPy backend requested but not available"
- Install CuPy for your CUDA version
- Or switch to "NumPy" or "Auto" backend

### "Cutline not applied / plugin errors reading cutline"
- The plugin reads cutline vectors via GDAL/OGR (`osgeo`) which is included with QGIS. If you provide an external file path, ensure the file is readable and has a valid CRS matching the DEM.

### Notes on dependencies
- The plugin avoids a Fiona dependency and instead uses the `osgeo` bindings bundled with QGIS. To install Python packages like `rasterio` or `cupy` into the QGIS Python environment, use the OSGeo4W shell or the QGIS Python executable.

### Computation is very slow
- Check available RAM; consider smaller tiles or limiting radius
- Try reducing ray directions
- Verify GPU is being used (check log messages)

### Memory errors during computation
- Reduce tile size
- Reduce max radius
- Reduce number of ray directions
- Process a smaller subset of the DEM

## Technical Details

### Algorithm
The plugin uses the horizon-angle visibility algorithm with:
- Tiled batch processing for memory efficiency
- Ray evaluation in multiple directions
- Optional CuPy GPU acceleration
- Cumulative visibility counting

### Performance
- **CPU (NumPy)**: ~1-10 million cells/minute (depends on hardware)
- **GPU (CuPy)**: ~10-100 million cells/minute (depends on GPU)

For a 2000x2000 DEM with 90 directions:
- CPU: ~4-40 minutes
- GPU: ~0.5-4 minutes

## License

This plugin is provided as-is for research and analysis purposes.

## Support

For issues or questions:
1. Check the computation log messages
2. Verify input data is valid and properly georeferenced
3. Test with smaller subsets to identify issues
4. Check QGIS Python console for detailed error messages

## References

The viewshed computation is based on horizon-angle algorithms for efficient visibility analysis.

---

**Version**: 1.1.0  
**Tested on**: QGIS 3.40+  
**Requires**: NumPy, Rasterio, GDAL/OGR (osgeo)  
**Optional**: CuPy (for GPU acceleration)
