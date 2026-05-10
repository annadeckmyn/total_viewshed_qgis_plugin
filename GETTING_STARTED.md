# Quick Start Guide

## 1. Install the Plugin

### Windows (Easiest)
```batch
# Run the installer in Command Prompt or PowerShell
.\install.bat
```

### Linux/Mac
```bash
# Run the installer in terminal
bash install.sh
```

### Manual Installation
1. Find your QGIS plugins directory:
   - **Windows**: `C:\Users\<USERNAME>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins`
   - **Linux**: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins`
   - **macOS**: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins`

2. Copy the entire `total_viewshed_qgis_plugin` folder to that directory

3. Restart QGIS

## 2. Enable in QGIS

1. Open QGIS
2. **Plugins → Manage and Install Plugins**
3. Search for "Total Viewshed"
4. Check the checkbox to enable
5. Click Close

## 3. Use the Plugin

1. Open QGIS with your DEM layer
2. **Total Viewshed → Total Viewshed** in the menu (or click toolbar icon)
3. Select your DEM layer
4. Set parameters (defaults are fine for most uses)
5. Click **Compute Viewshed**
6. Wait for computation to complete
7. Result automatically appears on your map

## Default Parameters (Recommended)

- **Ray Directions**: 90 (good balance)
- **Observer Height**: 1.6 m (eye level)
- **Max Radius**: 1200 m (typical landscape analysis)
- **Tile Size**: 256 pixels (good for 4-8 GB RAM)
- **Backend**: Auto (uses GPU if available)

## GPU Acceleration (Optional)

For faster computation on NVIDIA GPUs:

```bash
# In your QGIS Python environment, install CuPy
# Windows (OSGeo4W):
C:\Program Files\QGIS 3.44.9\bin\python-qgis-ltr.bat
pip install cupy-cuda12x

# Linux/Mac:
pip install cupy-cuda12x  # Or cupy-cuda11x for older CUDA
```

Then select "CuPy (GPU)" in the plugin dialog.

## Typical Use Cases

### Fast Preview
- Ray Directions: 45
- Max Radius: 800m
- Execution: ~1-5 minutes

### Standard Analysis
- Ray Directions: 90 (default)
- Max Radius: 1200m
- Execution: ~5-20 minutes

### High Accuracy
- Ray Directions: 180
- Max Radius: 1500m
- Execution: ~30-120 minutes (or ~3-15 minutes with GPU)

## Output

The plugin creates a GeoTIFF raster showing cumulative visible area:
- Default units: square meters
- With "Convert to hectares": hectares
- Georeferenced and ready for analysis

## Troubleshooting

**Plugin not visible in menu?**
- Verify installation path is correct
- Restart QGIS
- Check Python console for errors

**Computation too slow?**
- Reduce ray directions to 45-60
- Reduce max radius
- Use GPU backend if available
- Process smaller tile

**Memory errors?**
- Reduce tile size to 128 or 192
- Reduce max radius
- Use CPU (NumPy) backend to limit memory usage

**CuPy not working?**
- Check GPU drivers are up to date
- Verify CUDA installation
- Use "Auto" backend to fallback to CPU

See [README.md](README.md) for detailed documentation.
