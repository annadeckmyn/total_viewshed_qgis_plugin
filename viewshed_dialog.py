"""Dialog for Total Viewshed Calculator parameters."""

import os

from qgis.PyQt.QtCore import QThread
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QPushButton, QFileDialog, QLineEdit, QGroupBox,
    QMessageBox, QProgressBar, QTextEdit
)
from qgis.core import QgsRasterLayer, QgsProject, QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox

from .viewshed_worker import ViewshedWorker


class ViewshedDialog(QDialog):
    """Dialog for setting viewshed computation parameters."""

    def __init__(self, iface):
        """Initialize the dialog.

        Args:
            iface: QGIS interface
        """
        super().__init__()
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.worker = None
        self.worker_thread = None
        self.init_ui()

    def init_ui(self):
        """Create the user interface."""
        self.setWindowTitle('Total Viewshed Calculator')
        self.setGeometry(100, 100, 600, 800)
        main_layout = QVBoxLayout()

        # ---- Input Raster Selection ----
        input_group = QGroupBox('Input Raster')
        input_layout = QVBoxLayout()

        input_label = QLabel('DEM Layer:')
        self.dem_layer_combo = QgsMapLayerComboBox()
        self.dem_layer_combo.setFilters(
            QgsMapLayerProxyModel.RasterLayer
        )
        input_layout.addWidget(input_label)
        input_layout.addWidget(self.dem_layer_combo)

        chm_label = QLabel('CHM Layer (optional):')
        self.chm_layer_combo = QgsMapLayerComboBox()
        self.chm_layer_combo.setFilters(
            QgsMapLayerProxyModel.RasterLayer
        )
        self.chm_layer_combo.setAllowEmptyLayer(True)
        input_layout.addWidget(chm_label)
        input_layout.addWidget(self.chm_layer_combo)

        cutline_label = QLabel('Cutline Layer (optional):')
        self.cutline_layer_combo = QgsMapLayerComboBox()
        self.cutline_layer_combo.setFilters(
            QgsMapLayerProxyModel.VectorLayer
        )
        self.cutline_layer_combo.setAllowEmptyLayer(True)
        input_layout.addWidget(cutline_label)
        input_layout.addWidget(self.cutline_layer_combo)

        input_group.setLayout(input_layout)
        main_layout.addWidget(input_group)

        # ---- Parameters ----
        params_group = QGroupBox('Parameters')
        params_layout = QVBoxLayout()

        # Number of directions
        n_dirs_layout = QHBoxLayout()
        n_dirs_label = QLabel('Ray Directions (N_DIRS):')
        self.n_dirs_spin = QSpinBox()
        self.n_dirs_spin.setMinimum(4)
        self.n_dirs_spin.setMaximum(360)
        self.n_dirs_spin.setValue(90)
        self.n_dirs_spin.setSuffix(' directions')
        n_dirs_layout.addWidget(n_dirs_label)
        n_dirs_layout.addWidget(self.n_dirs_spin)
        n_dirs_layout.addStretch()
        params_layout.addLayout(n_dirs_layout)

        # Observer height
        obs_height_layout = QHBoxLayout()
        obs_height_label = QLabel('Observer Height:')
        self.observer_height_spin = QDoubleSpinBox()
        self.observer_height_spin.setMinimum(0.0)
        self.observer_height_spin.setMaximum(10.0)
        self.observer_height_spin.setValue(1.6)
        self.observer_height_spin.setSuffix(' m')
        self.observer_height_spin.setDecimals(2)
        obs_height_layout.addWidget(obs_height_label)
        obs_height_layout.addWidget(self.observer_height_spin)
        obs_height_layout.addStretch()
        params_layout.addLayout(obs_height_layout)

        # Max radius
        max_radius_layout = QHBoxLayout()
        max_radius_label = QLabel('Max Radius:')
        self.max_radius_spin = QSpinBox()
        self.max_radius_spin.setMinimum(0)
        self.max_radius_spin.setMaximum(10000)
        self.max_radius_spin.setValue(1200)
        self.max_radius_spin.setSuffix(' m (0 = unlimited)')
        max_radius_layout.addWidget(max_radius_label)
        max_radius_layout.addWidget(self.max_radius_spin)
        max_radius_layout.addStretch()
        params_layout.addLayout(max_radius_layout)

        # Tile size
        tile_size_layout = QHBoxLayout()
        tile_size_label = QLabel('Tile Size:')
        self.tile_size_spin = QSpinBox()
        self.tile_size_spin.setMinimum(64)
        self.tile_size_spin.setMaximum(1024)
        self.tile_size_spin.setValue(256)
        self.tile_size_spin.setSuffix(' pixels')
        tile_size_layout.addWidget(tile_size_label)
        tile_size_layout.addWidget(self.tile_size_spin)
        tile_size_layout.addStretch()
        params_layout.addLayout(tile_size_layout)

        # Convert to hectares
        self.hectares_check = QCheckBox('Convert result to hectares')
        self.hectares_check.setChecked(True)
        params_layout.addWidget(self.hectares_check)

        params_group.setLayout(params_layout)
        main_layout.addWidget(params_group)

        # ---- Backend Selection ----
        backend_group = QGroupBox('Computation Backend')
        backend_layout = QVBoxLayout()

        backend_label = QLabel('Select backend:')
        self.backend_combo = QComboBox()
        self.backend_combo.addItem('Auto (GPU if available)', 'auto')
        self.backend_combo.addItem('NumPy (CPU)', 'numpy')
        self.backend_combo.addItem('CuPy (GPU)', 'cupy')
        backend_layout.addWidget(backend_label)
        backend_layout.addWidget(self.backend_combo)

        backend_group.setLayout(backend_layout)
        main_layout.addWidget(backend_group)

        # ---- Output ----
        output_group = QGroupBox('Output')
        output_layout = QVBoxLayout()

        output_label = QLabel('Output File:')
        output_file_layout = QHBoxLayout()
        self.output_line = QLineEdit()
        self.output_line.setPlaceholderText('Leave empty to save in temp folder')
        browse_btn = QPushButton('Browse...')
        browse_btn.clicked.connect(self.browse_output)
        output_file_layout.addWidget(self.output_line)
        output_file_layout.addWidget(browse_btn)

        output_layout.addWidget(output_label)
        output_layout.addLayout(output_file_layout)

        self.load_to_canvas_check = QCheckBox('Load result to canvas')
        self.load_to_canvas_check.setChecked(True)
        output_layout.addWidget(self.load_to_canvas_check)

        output_group.setLayout(output_layout)
        main_layout.addWidget(output_group)

        # ---- Progress ----
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # ---- Log ----
        log_label = QLabel('Computation Log:')
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(100)
        main_layout.addWidget(log_label)
        main_layout.addWidget(self.log_text)

        # ---- Buttons ----
        button_layout = QHBoxLayout()

        self.compute_btn = QPushButton('Compute Viewshed')
        self.compute_btn.clicked.connect(self.start_computation)
        button_layout.addWidget(self.compute_btn)

        self.cancel_btn = QPushButton('Cancel')
        self.cancel_btn.clicked.connect(self.cancel_computation)
        self.cancel_btn.setVisible(False)
        button_layout.addWidget(self.cancel_btn)

        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    def browse_output(self):
        """Browse for output file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, 'Save Viewshed Output', '', 'GeoTIFF (*.tif);;All Files (*)'
        )
        if file_path:
            if not file_path.endswith('.tif'):
                file_path += '.tif'
            self.output_line.setText(file_path)

    def log_message(self, message):
        """Add a message to the log."""
        self.log_text.append(message)
        # Auto-scroll to bottom
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def start_computation(self):
        """Start the viewshed computation in a worker thread."""
        # Validate inputs
        dem_layer = self.dem_layer_combo.currentLayer()
        if dem_layer is None:
            QMessageBox.warning(self, 'Error', 'Please select a DEM layer.')
            return

        if not isinstance(dem_layer, QgsRasterLayer):
            QMessageBox.warning(self, 'Error', 'Selected layer is not a raster.')
            return

        # Get parameters
        dem_path = dem_layer.source()
        chm_layer = self.chm_layer_combo.currentLayer()
        chm_path = chm_layer.source() if chm_layer else None
        cutline_layer = self.cutline_layer_combo.currentLayer()
        cutline_path = cutline_layer.source() if cutline_layer else None

        output_path = self.output_line.text()
        if not output_path:
            # Use temp file
            import tempfile
            temp_dir = tempfile.gettempdir()
            output_path = os.path.join(temp_dir, 'viewshed_output.tif')

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Disable button and show cancel
        self.compute_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)

        # Clear log
        self.log_text.clear()

        # Create and start worker thread
        self.worker_thread = QThread()
        self.worker = ViewshedWorker(
            dem_path=dem_path,
            chm_path=chm_path,
            output_path=output_path,
            n_dirs=self.n_dirs_spin.value(),
            observer_height=self.observer_height_spin.value(),
            max_radius_meters=self.max_radius_spin.value() or None,
            tile_size=self.tile_size_spin.value(),
            backend=self.backend_combo.currentData(),
            convert_to_hectares=self.hectares_check.isChecked(),
            cutline_path=cutline_path,
        )

        self.worker.moveToThread(self.worker_thread)
        self.worker.finished.connect(self.on_computation_finished)
        self.worker.error.connect(self.on_computation_error)
        self.worker.progress.connect(self.on_progress)
        self.worker.log.connect(self.log_message)
        self.worker_thread.started.connect(self.worker.run)
        self.worker_thread.start()

    def on_progress(self, value):
        """Handle progress updates."""
        self.progress_bar.setValue(value)

    def on_computation_finished(self, output_path):
        """Handle computation completion."""
        self.compute_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.worker_thread.quit()
        self.worker_thread.wait()

        self.log_message(f'Viewshed saved to: {output_path}')

        if self.load_to_canvas_check.isChecked():
            # Load the result to canvas
            layer = QgsRasterLayer(output_path, 'Total Viewshed')
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
                self.canvas.zoomToFullExtent()
                self.log_message('Layer loaded to canvas.')
            else:
                self.log_message('Warning: Could not load layer to canvas.')

        QMessageBox.information(
            self, 'Success', f'Viewshed computation completed!\n\nOutput: {output_path}'
        )

    def on_computation_error(self, error_message):
        """Handle computation errors."""
        self.compute_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.worker_thread.quit()
        self.worker_thread.wait()

        self.log_message(f'Error: {error_message}')
        QMessageBox.critical(self, 'Computation Error', error_message)

    def cancel_computation(self):
        """Cancel the computation."""
        if self.worker:
            self.worker.stop()
        self.compute_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.log_message('Computation cancelled.')
