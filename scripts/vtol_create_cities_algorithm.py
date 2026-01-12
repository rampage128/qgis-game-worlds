from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsWkbTypes,
    QgsField,
    QgsFields,
    QgsProcessingParameterVectorDestination,
    QgsFieldConstraints,
    QgsProcessingUtils,
    QgsVectorLayer,
    QgsEditorWidgetSetup,
    QgsSymbol,
    QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsFeatureRequest,
    QgsExpression,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterMatrix,
    QgsFeature,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessing,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsProject,
)
from qgis.PyQt.QtCore import QMetaType, QVariant, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QDoubleSpinBox,
    QStyledItemDelegate,
    QHeaderView,
)
from qgis import processing
from typing import cast, Optional
from osgeo import gdal, osr
from qgis.gui import QgsAbstractProcessingParameterWidgetWrapper, QgsProcessingGui


class VtolCreateCitiesAlgorithm(QgsProcessingAlgorithm):
    OUTPUT = "OUTPUT"
    PARAMETER_MAP_AREA = "MAP_AREA"
    PARAMETER_GENERATE_CITIES = "GENERATE_CITIES"
    PARAMETER_CITY_LEVELS = "CITY_LEVELS"

    CITY_LEVEL_MAP = {
        1: "Rural",
        2: "Suburb",
        3: "Midtown",
        4: "Downtown I",
        5: "Downtown II",
    }

    CITY_COLOR_MAP = {
        1: "#00BB00",
        2: "#99FF99",
        3: "#FFFF00",
        4: "#FF9900",
        5: "#FF0000",
    }

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PARAMETER_MAP_AREA,
                self.tr("<b>Map Information</b><br><br>Map Area"),
                [QVariant.Int, 3],  # QgsWkbTypes.Polygon
            )
        )

        city_levels = QgsProcessingParameterMatrix(
            self.PARAMETER_CITY_LEVELS,
            "<hr><br><b>City Generation</b><br><br>City Levels",
            hasFixedNumberRows=True,
            numberRows=6,
            headers=["Height"],
            defaultValue=[2.5, 4, 8, 18, 45, 1000],
        )
        city_levels.setMetadata({**city_levels.metadata(), "widget_wrapper": CityTable})
        self.addParameter(city_levels)

        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT, self.tr("<hr><br><b>Output</b><br><br>City Zones")
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        city_levels: list[float] = self.parameterAsMatrix(
            parameters, self.PARAMETER_CITY_LEVELS, context
        )
        skip_city_generation = not any(city_levels)

        map_area_layer = self.parameterAsVectorLayer(
            parameters, self.PARAMETER_MAP_AREA, context
        )

        if map_area_layer is None:
            raise QgsProcessingException(self.tr("Provided map area layer not found!"))

        feature_iterator = map_area_layer.getFeatures()
        area: Optional[QgsFeature] = next(feature_iterator, None)
        if area is None:
            raise QgsProcessingException(
                self.tr("No feature found in provided map area layer!")
            )

        fields = QgsFields()
        fields.append(QgsField("City Level", QMetaType.Int, "integer", 5, 0))

        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.Polygon,
            map_area_layer.crs(),
        )

        if sink is None:
            raise QgsProcessingException(self.tr("Invalid output sink provided."))

        # Force the sink to write the feature and auto-load the layer into the project
        del sink

        output_layer = cast(
            QgsVectorLayer | None,
            QgsProcessingUtils.mapLayerFromString(dest_id, context),
        )

        if not isinstance(output_layer, QgsVectorLayer):
            raise QgsProcessingException("Generated layer is not a valid vector layer.")

        field_index = output_layer.fields().indexOf("City Level")

        city_level_map = {
            f"{k}. {v}": str(k) for k, v in sorted(self.CITY_LEVEL_MAP.items())
        }
        widget_setup = QgsEditorWidgetSetup("ValueMap", {"map": city_level_map})
        output_layer.setEditorWidgetSetup(field_index, widget_setup)

        output_layer.setFieldConstraint(
            field_index,
            QgsFieldConstraints.Constraint.ConstraintNotNull,
            QgsFieldConstraints.ConstraintStrength.ConstraintStrengthHard,
        )

        color_categories = []
        for value, label in self.CITY_LEVEL_MAP.items():
            color = QColor(self.CITY_COLOR_MAP.get(value, "#AAAAAA"))
            # Create a simple fill symbol for each category
            symbol = QgsSymbol.defaultSymbol(output_layer.geometryType())
            symbol.setColor(color)
            symbol.setOpacity(0.7)

            category = QgsRendererCategory(value, symbol, label)
            color_categories.append(category)

        renderer = QgsCategorizedSymbolRenderer("City Level", color_categories)
        renderer.setOrderByEnabled(True)
        renderer.setOrderBy(
            QgsFeatureRequest.OrderBy(
                [
                    QgsFeatureRequest.OrderByClause(
                        QgsExpression('"City Level"'), True, False
                    )
                ]
            )
        )
        output_layer.setRenderer(renderer)

        output_layer.commitChanges()

        if not skip_city_generation:
            self._generate_cities(
                map_area_layer, area, output_layer, city_levels, feedback, context
            )
        else:
            feedback.pushInfo("Skipping city generation (user choice).")

        context.addLayerToLoadOnCompletion(
            dest_id,
            QgsProcessingContext.LayerDetails(
                f"{area["name"]} (cities)", context.project()
            ),
        )

        return {self.OUTPUT: dest_id}

    def _generate_cities(
        self,
        map_area_layer: QgsVectorLayer,
        area: QgsFeature,
        city_layer: QgsVectorLayer,
        city_levels: list[float],
        feedback: QgsProcessingFeedback,
        context: QgsProcessingContext,
    ):
        transform = QgsCoordinateTransform(
            map_area_layer.crs(),
            QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsProject.instance(),
        )
        extent = transform.transformBoundingBox(map_area_layer.extent())

        download_data = QgsProcessingUtils.generateTempFilename("city_source.tif")

        base_url = (
            "/vsizip//vsicurl/https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL"
        )
        # built_s_url = "GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2025_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_E2025_GLOBE_R2023A_4326_3ss_V1_0.zip/GHS_BUILT_S_E2025_GLOBE_R2023A_4326_3ss_V1_0.tif"
        built_h_url = "GHS_BUILT_H_GLOBE_R2023A/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_4326_3ss_V1_0.zip/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_4326_3ss_V1_0.tif"
        source_url = f"{base_url}/{built_h_url}"

        gdal.SetConfigOption("GDAL_HTTP_UNSAFESSL", "YES")
        gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".zip,.tif")

        gdal.SetConfigOption("VSI_CACHE", "TRUE")
        gdal.SetConfigOption("VSI_CACHE_SIZE", "50000000")
        gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
        gdal.SetConfigOption("GDAL_HTTP_MULTIPLEX", "YES")
        gdal.SetConfigOption("GDAL_HTTP_VERSION", "2")

        feedback.setProgressText(f'Downloading building data for area "{area["name"]}"')
        feedback.pushInfo(f"Using extent of area {area["name"]}: {extent.toString()}")

        ds = gdal.Open(source_url)
        if not ds:
            raise QgsProcessingException("JRC Server Handshake Failed.")

        try:
            gt = ds.GetGeoTransform()
            inv_gt = gdal.InvGeoTransform(gt)

            # Extent to Pixel Space
            off_x, off_y = gdal.ApplyGeoTransform(
                inv_gt, extent.xMinimum(), extent.yMaximum()
            )
            end_x, end_y = gdal.ApplyGeoTransform(
                inv_gt, extent.xMaximum(), extent.yMinimum()
            )

            def gdal_callback(dfComplete, pszMessage, pData):
                feedback = pData
                if feedback.isCanceled():
                    return 0
                feedback.setProgress(int(dfComplete * 100))
                return 1

            band = ds.GetRasterBand(1)

            # Fetching windowed data
            data = band.ReadAsArray(
                int(off_x),
                int(off_y),
                int(end_x - off_x),
                int(end_y - off_y),
                callback=gdal_callback,
                callback_data=feedback,
            )

            # New subset geotransform
            new_gt = (extent.xMinimum(), gt[1], 0, extent.yMaximum(), 0, gt[5])

            # Get dimensions from the array
            rows, cols = data.shape

            # Create the file (1 band, Byte/Float based on your data)
            driver = gdal.GetDriverByName("GTiff")
            out_ds = driver.Create(download_data, cols, rows, 1, gdal.GDT_Float32)

            # Set the spatial anchor
            out_ds.SetGeoTransform(new_gt)

            # Set the projection (WGS84 / EPSG:4326)
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            out_ds.SetProjection(srs.ExportToWkt())

            # Write the pixel data
            band = out_ds.GetRasterBand(1)
            band.WriteArray(data)

            # Flush to disk
            band.FlushCache()
            out_ds = None
        except Exception as e:
            raise QgsProcessingException(f"Error downloading city data: {e}")
        finally:
            ds = None

        if feedback.isCanceled():
            return
        feedback.setProgressText(f"Reprojecting city data")

        city_resolution = 153.6
        seed_data = processing.run(
            "gdal:warpreproject",
            {
                "INPUT": download_data,
                "DATA_TYPE": 0,
                "RESAMPLING": 6,
                "TARGET_CRS": map_area_layer.crs(),
                "TARGET_RESOLUTION": city_resolution,
                "INIT_DEST": 0,
                "MULTITHREADING": True,
                "NODATA": 0,
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            feedback=feedback,
            context=context,
            is_child_algorithm=True,
        )["OUTPUT"]

        if feedback.isCanceled():
            return
        feedback.setProgressText(f"Classifying building height")

        reclass_table = []
        cutoff = city_levels[0]
        for i in range(1, len(city_levels)):
            height = city_levels[i]
            if height <= 0:
                continue
            reclass_table.extend([cutoff, height, i])
            cutoff = height

        classified_data = processing.run(
            "native:reclassifybytable",
            {
                "INPUT_RASTER": seed_data,
                "RASTER_BAND": 1,
                "TABLE": reclass_table,
                "NO_DATA": 0,
                "RANGE_BOUNDARIES": 0,
                "NODATA_FOR_MISSING": True,
                "DATA_TYPE": 2,
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            feedback=feedback,
            context=context,
            is_child_algorithm=True,
        )["OUTPUT"]

        if feedback.isCanceled():
            return
        feedback.setProgressText(f"Smoothing city shapes")

        vector_data = processing.run(
            "grass7:r.to.vect",
            {
                "input": classified_data,
                "type": 2,
                "column": "level",
                "-s": True,
                "output": QgsProcessing.TEMPORARY_OUTPUT,
            },
            feedback=feedback,
            context=context,
            is_child_algorithm=True,
        )["output"]

        city_level_index = city_layer.fields().indexOf("City Level")
        feature_layer = QgsVectorLayer(vector_data, "temp_grass_output", "ogr")

        if not feature_layer.isValid():
            raise QgsProcessingException(f"Invalid feature Layer: {vector_data}")

        total_features = feature_layer.featureCount()
        if total_features == 0:
            feedback.pushWarning(f"No features found for area {area["name"]}!")
            return

        if feedback.isCanceled():
            return
        feedback.setProgressText(f"Processing city features")

        city_features = []
        for index, feature in enumerate(feature_layer.getFeatures()):
            if feedback.isCanceled():
                break

            feedback.setProgress(int(total_features / (index + 1)))

            city_level = feature["level"]
            geometry = feature.geometry()
            if city_level == 1 and geometry.area() <= 95000:
                continue

            new_feat = QgsFeature(city_layer.fields())
            new_feat.setGeometry(geometry)
            new_feat.setAttribute(city_level_index, feature["level"])
            city_features.append(new_feat)

        city_layer.startEditing()
        city_layer.dataProvider().addFeatures(city_features)
        city_layer.commitChanges()
        city_layer.triggerRepaint()

    def name(self):
        return "vtol_cities_creator"

    def displayName(self):
        return self.tr("Create city zones")

    def group(self):
        return self.tr("VTOL VR")

    def groupId(self):
        return "vtol_vr_maps"

    def tr(self, message):
        return message

    def createInstance(self):
        return VtolCreateCitiesAlgorithm()

    def shortHelpString(self) -> str:
        return (
            "Creates a new VTOL VR city layer.<br><br>"
            "The output is returned as a new empty vector-layer. It can be used to draw your cities on.<br><br>"
            "Cities will automatically be sorted by city level (higher levels render over lower levels). "
            "You can also overlap shapes and the export will take care of it."
            "<h3>Parameters</h3>"
            "<h4>Map Information</h4>"
            "<ul>"
            '<li><b>Map Area:</b> Select the map area layer you have created with "Create Map Area" before.</li>'
            "</ul>"
            "<h4>City Generation</h4>"
            "Generates cities based on global real-world building height values.<br>"
            "Uncheck all entries if you want to skip automatic generation."
            "<ul>"
            "<li><b>City Levels:</b> Select the city levels you want to generate.</li>"
            "<li><b>Height (m):</b> Maximum height of buildings in meters that are considered for the correspondiung level.</li>"
            "<li><b>Cutoff:</b> All buildings lower than this value will not be considered as cities.</li>"
            "</ul>"
            "<h4>Output</h4>"
            "<ul>"
            "<li><b>City Zones:</b> Path to where to store the final data. I highly recommend saving this as a file.</li>"
            "</ul>"
            "After the algorithm has completed the output will appear as a new layer."
            "<h3>How to draw cities?</h3>"
            "<ul>"
            "<li>Run this algorithm.</li>"
            '<li>Make sure the "Digitizing Toolbar" and "Advanced Digitizing Toolbar" are enabled. (right click on an empty space in the top toolbar to enable it)</li>'
            '<li>Right click the "City Zones" layer in the "Layers" panel and select "Toggle Editing" (or click the yellow pen in the Digitizing Toolbar).</li>'
            '<li>Select "Add Polygon Feature" button in the "Digitizing Toolbar".</li>'
            '<li>Make sure next to "Add Polygon Feature" the correct mode is selected (for example "Digitize with Segment").</li>'
            "<li>Draw a shape on the map and finish drawing with right click.</li>"
            "<li>A dialog will ask you to select the city type.</li>"
            '<li>Click "OK" to save or "Cancel" to discard the shape.</li>'
            '<li>When done drawing click the yellow pen in the "Digitizing Toolbar" again. Make sure to select "save" in the prompt.</li>'
            "</ul>"
            '<b>NOTE:</b> You can edit shapes with the "Vertex Tool" in the "Digitizing Toolbar".'
            'For a more elaborate documentation on QGIS vector editing see: <a href="https://docs.qgis.org/3.40/en/docs/user_manual/working_with_vector/editing_geometry_attributes.html#digitizing-an-existing-layer">QGIS layer digitizing</a>.'
        )


class CityTable(QgsAbstractProcessingParameterWidgetWrapper):

    LEVEL_NAMES = [
        "Cutoff",
        "Rural",
        "Suburban",
        "Midtown",
        "Downtown I",
        "Downtown II",
    ]
    LEVEL_DEFAULTS = [2.5, 4, 8, 18, 45, 1000]

    def __init__(self, parameter, dialog, row, col, **kwargs):
        super().__init__(parameter, QgsProcessingGui.WidgetType.Standard)

        self.setDialog(dialog)
        self.alg_dialog = dialog
        self.value = [*self.LEVEL_DEFAULTS]

    def createWidget(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.container = container
        self.layout = layout

        table = QTableWidget(6, 2)
        table.setHorizontalHeaderLabels(["Level", "Height (m)"])
        delegate = SpinBoxDelegate(table)
        table.setItemDelegateForColumn(1, delegate)

        for row, name in enumerate(self.LEVEL_NAMES):
            check_item = QTableWidgetItem(name)
            check_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check_item.setCheckState(Qt.Checked)
            table.setItem(row, 0, check_item)

            height_item = QTableWidgetItem(str(self.value[row]))
            table.setItem(row, 1, height_item)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        total_height = (
            table.horizontalHeader().height()
            + table.verticalHeader().length()
            + (table.frameWidth() * 2)
        )
        table.setFixedHeight(total_height)
        table.verticalHeader().setVisible(False)

        self.table = table
        table.itemChanged.connect(self._item_changed)
        table.mousePressEvent = self._table_clicked

        layout.addWidget(table)

        return self.container

    def _table_clicked(self, event):
        table = self.table
        index = table.indexAt(event.pos())

        if index.isValid() and index.column() == 0:
            item = table.item(index.row(), index.column())
            new_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
            item.setCheckState(new_state)
        else:
            QTableWidget.mousePressEvent(table, event)

    def _item_changed(self, item):
        table = self.table
        table.blockSignals(True)
        new_values = []
        for row in range(table.rowCount()):
            active = table.item(row, 0).checkState() == Qt.Checked
            try:
                height = float(table.item(row, 1).text()) if active else 0
            except ValueError:
                height = self.LEVEL_DEFAULTS[row]
            new_values.append(height)
            table.item(row, 1).setFlags(
                (Qt.ItemIsEnabled | Qt.ItemIsEditable | Qt.ItemIsSelectable)
                if active
                else Qt.NoItemFlags
            )

        table.blockSignals(False)

        self.value = new_values
        self.widgetValueHasChanged.emit(self)

    def setWidgetValue(self, value, is_changed=False):
        self.value = value

    def widgetValue(self):
        return self.value

    def setWidgetContext(self, context):
        super().setWidgetContext(context)


class SpinBoxDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QDoubleSpinBox(parent)
        editor.setFrame(False)
        editor.setMinimum(0)
        editor.setMaximum(9999)
        editor.setDecimals(1)
        return editor

    def setEditorData(self, editor, index):
        value = float(index.model().data(index, Qt.EditRole))
        editor.setValue(value)

    def setModelData(self, editor, model, index):
        editor.interpretText()
        value = editor.value()
        model.setData(index, value, Qt.EditRole)
