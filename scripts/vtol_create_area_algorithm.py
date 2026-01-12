from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsGeometry,
    QgsWkbTypes,
    QgsFeature,
    QgsField,
    QgsProject,
    QgsFields,
    QgsPointXY,
    QgsProcessingParameterString,
    QgsProcessingUtils,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsPalLayerSettings,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterExtent,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRasterLayer,
    QgsAnnotationLayer,
    QgsAnnotationMarkerItem,
    QgsAnnotationPointTextItem,
    QgsAnnotationPolygonItem,
    QgsPoint,
    QgsFillSymbol,
    QgsTextFormat,
    QgsCurvePolygon,
    QgsMarkerSymbol,
    QgsProcessingContext,
)
from qgis.gui import (
    QgsAbstractProcessingParameterWidgetWrapper,
    QgsMapCanvas,
    QgsProcessingGui,
    QgsMapToolEmitPoint,
)
from qgis.PyQt.QtCore import QMetaType, Qt, pyqtSignal
from qgis.PyQt.QtGui import QPainter
from qgis.PyQt.QtWidgets import QWidget, QVBoxLayout, QSizePolicy, QSlider, QComboBox

from qgis.utils import iface
import math


class VtolCreateAreaAlgorithm(QgsProcessingAlgorithm):
    PARAMETER_MAP_NAME = "PARAMETER_MAP_NAME"
    PARAMETER_BIOME = "PARAMETER_BIOME"
    PARAMETER_EDGE = "PARAMETER_EDGE"
    PARAMETER_COAST = "PARAMETER_COAST"
    PARAMETER_CENTER_POINT = "PARAMETER_CENTER_POINT"
    PARAMETER_SIZE = "PARAMETER_SIZE"
    PARAMETER_IMPROVE_GPS = "PARAMETER_IMPROVE_GPS"

    PARAMETER_REFERENCE_EXTENT = "REFERENCE_EXTENT"

    OUTPUT = "OUTPUT"

    # SIZE_OPTIONS = list(MAP_SIZES.keys())
    BIOME_OPTIONS = ["Boreal", "Desert", "Arctic"]
    EDGE_OPTIONS = ["Water", "Hills", "Coast"]
    COAST_OPTIONS = ["North", "South", "East", "West"]

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterString(
                self.PARAMETER_MAP_NAME,
                self.tr("<hr><br><b>Area</b><br><br>Map Name"),
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAMETER_BIOME,
                self.tr("Biome"),
                options=self.BIOME_OPTIONS,
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAMETER_EDGE,
                self.tr("Edge"),
                options=self.EDGE_OPTIONS,
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAMETER_COAST,
                self.tr("Coast Side (only used if Edge = Coast)"),
                options=self.COAST_OPTIONS,
                defaultValue=0,
            )
        )

        extent_parameter = QgsProcessingParameterExtent(
            self.PARAMETER_REFERENCE_EXTENT,
            self.tr(
                "<hr><br><b>Location</b><br><br>Select a location by clicking on the map and set your map size with the slider below"
            ),
        )
        extent_parameter.setMetadata(
            {**extent_parameter.metadata(), "widget_wrapper": Highlighter}
        )

        self.addParameter(extent_parameter)

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAMETER_IMPROVE_GPS,
                self.tr("Improve GPS"),
                defaultValue=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("<hr><br><b>Output</b><br><br>Save as"),
            )
        )

    def processAlgorithm(self, parameters, context, feedback):

        improve_gps = self.parameterAsBoolean(
            parameters, self.PARAMETER_IMPROVE_GPS, context
        )

        # Inside run()
        user_extent = self.parameterAsExtent(
            parameters, self.PARAMETER_REFERENCE_EXTENT, context
        )
        user_crs = self.parameterAsExtentCrs(
            parameters, self.PARAMETER_REFERENCE_EXTENT, context
        )

        calculator = UtmMapAreaCalculator()
        map_extent, map_crs, chunks, map_size, corner_wgs84 = calculator.fromExtent(
            user_extent, user_crs
        )

        map_name = self.parameterAsString(parameters, self.PARAMETER_MAP_NAME, context)

        biome = self.BIOME_OPTIONS[
            self.parameterAsEnum(parameters, self.PARAMETER_BIOME, context)
        ]
        edge = self.EDGE_OPTIONS[
            self.parameterAsEnum(parameters, self.PARAMETER_EDGE, context)
        ]
        coast = self.COAST_OPTIONS[
            self.parameterAsEnum(parameters, self.PARAMETER_COAST, context)
        ]

        # TODO: Limit field choices for editing and consistency (value-maps, ranges)
        # TODO: Maybe make chunks and size non-editable or calculate them as layer expression fields
        fields = QgsFields()
        fields.append(QgsField("name", QMetaType.QString, "text", 255, 0))
        fields.append(QgsField("size", QMetaType.Int, "integer", 10, 0))
        fields.append(QgsField("chunks", QMetaType.Int, "integer", 10, 0))
        fields.append(QgsField("biome", QMetaType.QString, "text", 255, 0))
        fields.append(QgsField("edge", QMetaType.QString, "text", 255, 0))
        fields.append(QgsField("coast", QMetaType.QString, "text", 255, 0))

        layer_name_output = f"Map-Area: {map_name}"

        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.Polygon,
            map_crs,
        )

        if sink is None:
            raise QgsProcessingException(self.tr("Invalid output sink provided."))

        # 2. Create and fill the feature
        new_feature = QgsFeature(fields)
        new_feature.setGeometry(QgsGeometry.fromRect(map_extent))
        new_feature["name"] = map_name
        new_feature["size"] = map_size
        new_feature["chunks"] = chunks
        new_feature["biome"] = biome
        new_feature["edge"] = edge
        new_feature["coast"] = coast

        sink.addFeature(new_feature)
        # Force the sink to write the feature and auto-load the layer into the project
        del sink

        output_layer = QgsProcessingUtils.mapLayerFromString(dest_id, context)

        if not isinstance(output_layer, QgsVectorLayer):
            raise QgsProcessingException("Generated layer is not a valid vector layer.")

        output_layer.setBlendMode(QPainter.CompositionMode_HardLight)
        output_layer.setOpacity(0.3)
        output_layer.setName(layer_name_output)

        dataProvider = output_layer.dataProvider()
        # Check if the provider can handle adding attributes (required for virtual fields)
        if dataProvider and (dataProvider.capabilities() & dataProvider.AddAttributes):

            # Start editing session is essential for file-backed layers (like GeoPackage)
            # to accept new field definitions persistently.
            output_layer.startEditing()

            gps_correction = ""
            if improve_gps:
                deg_at_e = 111320
                gps_correction = (
                    f" + with_variable('phi_deg', "
                    f"y(transform(centroid($geometry), @layer_crs, 'EPSG:4326')), "
                    f"({map_size} / (2 * {deg_at_e})) * ((1 / cos(radians(@phi_deg))) - 1)"
                    f")"
                )

            # lon and lat are the south-west corner of the map area (as referenced ingame)
            lon_expr = f"x(transform(point_n($geometry, 1), @layer_crs, 'EPSG:4326')){gps_correction}"
            lat_expr = "y(transform(point_n($geometry, 1), @layer_crs, 'EPSG:4326'))"

            # Add virtual fields
            success_lon = output_layer.addExpressionField(
                lon_expr, QgsField("longitude", QMetaType.Double, "Real", 15, 6)
            )
            success_lat = output_layer.addExpressionField(
                lat_expr, QgsField("latitude", QMetaType.Double, "Real", 15, 6)
            )

            if success_lon and success_lat:
                feedback.pushInfo(
                    "Successfully added virtual fields 'longitude' and 'latitude'. These fields will update dynamically."
                )
            else:
                feedback.pushInfo(
                    "[ERROR] Failed to add virtual fields. Check QGIS Log for expression errors."
                )

            # 1. Enable Labeling
            settings = QgsPalLayerSettings()
            settings.isExpression = True
            settings.enabled = True
            settings.drawLabels = True
            settings.placement = QgsPalLayerSettings.Placement.AroundPoint
            settings.fieldName = (
                "'Area ' || \"name\" || '\\n' || "
                "round(\"size\" / 1000) || 'km (' || \"chunks\" || ' Chunks)' || '\\n\\n' || "
                "'Longitude: ' || \"longitude\" || '\\n' || "
                "'Latitude: ' || \"latitude\" || '\\n\\n' || "
                "\"biome\" || ', ' || \"edge\" || ' (' || \"coast\" || ')'"
            )

            fmt = QgsTextFormat()
            fmt.setSize(10)
            # fmt.setAllowHtmlFormatting(True)
            # Optional: add a buffer/halo for visibility over OSM
            fmt.buffer().setEnabled(True)
            fmt.buffer().setSize(1)
            settings.setFormat(fmt)

            # 3. Apply the Settings to the Layer
            renderer = QgsVectorLayerSimpleLabeling(settings)
            output_layer.setLabeling(renderer)
            output_layer.setLabelsEnabled(True)

            # Commit changes to persist the new fields metadata to the file/data source
            output_layer.commitChanges()

        # Force UI update
        output_layer.updateFields()
        output_layer.triggerRepaint()

        self.layer_name = layer_name_output
        self.dest_id = dest_id

        context.addLayerToLoadOnCompletion(
            dest_id,
            QgsProcessingContext.LayerDetails(f"{map_name} (area)", context.project()),
        )

        return {self.OUTPUT: dest_id}

    def postProcessAlgorithm(self, context, feedback):
        output_layer = QgsProcessingUtils.mapLayerFromString(self.dest_id, context)

        if not isinstance(output_layer, QgsVectorLayer):
            raise QgsProcessingException("Generated layer is not a valid vector layer.")

        QgsProject.instance().setCrs(output_layer.crs())
        view = QgsRectangle(output_layer.extent())
        view.grow(output_layer.extent().width() * 0.1)
        iface.mapCanvas().setExtent(view)

        return super().postProcessAlgorithm(context, feedback)

    def name(self):
        return "vtol_area_creator"

    def displayName(self):
        return self.tr("Create map area")

    def group(self):
        return self.tr("VTOL VR")

    def groupId(self):
        return "vtol_vr_maps"

    def tr(self, message):
        return message

    def createInstance(self):
        return VtolCreateAreaAlgorithm()

    def shortHelpString(self) -> str:
        return (
            "Creates a new VTOL VR Map Area.<br><br>"
            "The output is returned as a new vector-layer with a square. It can be used to export a region of your data."
            "<h3>Parameters</h3>"
            "<h4>Area</h4>"
            "<ul>"
            "<li><b>Map Name:</b> A display name for your map area.</li>"
            "<li><b>Biome:</b> The VTOL VR map biome.</li>"
            "<li><b>Edge:</b> What the map edge should be filled with.</li>"
            '<li><b>Coast Side:</b> If Map Edge is set to "Coast", this decides on which side the coast should be.</li>'
            "</ul>"
            "<h4>Location</h4>"
            "<ul>"
            "<li>Pick a center point for your mission area on the map.</li>"
            "<li>Change the area size with the slider under the map.</li>"
            "<li><b>improve GPS:</b> GPS positions ingame are calculated wrong. This improves accuracy a bit by faking a different longitude.</li>"
            "</ul>"
            "<h4>Output</h4>"
            "I recommend saving this to a file.\n"
            "The output will be a vector layer.\n\n"
            "After the algorithm has completed the output will appear as a new layer. "
            "<h3>Adjusting the map position.</h3>"
            "Ideally you should not change the map position after it has been created, because it is tied to its projection."
            "You can however make small adjustments after the fact:"
            "<ul>"
            '<li>Make sure the "Digitizing Toolbar" and "Advanced Digitizing Toolbar" are enabled. (right click on an empty space in the top toolbar to enable it)</li>'
            '<li>Right click the map area layer in the "Layers" panel and select "Toggle Editing" (Or click the yellow pen in the Digitizing Toolbar)</li>'
            '<li>Select "Move Feature" button in the "Advanced Digitizing Toolbar".</li>'
            "<li>Click and drag the area square on the map view. A red outline will show where you are moving it.</li>"
            "</ul>"
            "<b>NOTE:</b> The latitude and longitude should automatically update after moving."
        )


class UtmMapAreaCalculator:
    def fromExtent(self, extent: QgsRectangle, crs: QgsCoordinateReferenceSystem):
        # 1. Get center in WGS84 to determine zone
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        to_wgs84 = QgsCoordinateTransform(crs, wgs84, QgsProject.instance())
        center_wgs84 = to_wgs84.transform(extent.center())
        corner_wgs84 = to_wgs84.transform(
            QgsPointXY(extent.xMinimum(), extent.yMinimum())
        )

        # 2. Calculate UTM EPSG (326XX for North, 327XX for South)
        zone = int((center_wgs84.x() + 180) / 6) + 1
        hemisphere = 32600 if center_wgs84.y() >= 0 else 32700
        utm_crs = QgsCoordinateReferenceSystem.fromEpsgId(hemisphere + zone)

        # 3. Transform to UTM and buffer
        to_utm = QgsCoordinateTransform(crs, utm_crs, QgsProject.instance())

        extent_utm = to_utm.transformBoundingBox(extent)
        user_size = min(extent_utm.width(), extent_utm.height())
        chunks = max(8, min(64, int(user_size / 3072.0)))
        map_size = chunks * 3072
        half_size = map_size / 2.0
        center_utm = to_utm.transform(extent.center())

        utm_extent = QgsRectangle(
            center_utm.x() - half_size,
            center_utm.y() - half_size,
            center_utm.x() + half_size,
            center_utm.y() + half_size,
        )
        return (utm_extent, utm_crs, chunks, map_size, corner_wgs84)


class CanvasClickTool(QgsMapToolEmitPoint):
    pointChanged = pyqtSignal(object)
    active = False

    def canvasPressEvent(self, e):
        self.active = True

    def canvasMoveEvent(self, event):
        if self.active:
            self.pointChanged.emit(event.mapPoint())

    def canvasReleaseEvent(self, event):
        self.active = False
        self.pointChanged.emit(event.mapPoint())


class Highlighter(QgsAbstractProcessingParameterWidgetWrapper):

    MAP_SOURCES = {
        "Google Terrain Hybrid": "type=xyz&url=https://mt1.google.com/vt/lyrs%3Dp%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D&zmax=19&zmin=0",
        "Google Satellite Hybrid": "type=xyz&url=https://mt1.google.com/vt/lyrs%3Dy%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D&zmax=19&zmin=0",
        "OpenStreetMap": "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0",
    }

    def __init__(self, parameter, dialog, row, col, **kwargs):
        super().__init__(parameter, QgsProcessingGui.WidgetType.Standard)

        self.rb = None
        self.user_extent: QgsRectangle | None = None
        self.setDialog(dialog)
        self.alg_dialog = dialog
        self.label = None
        self.point = QgsPointXY()
        self.chunks = 32

    def createWidget(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.container = container
        self.layout = layout

        self.anno_layer = QgsAnnotationLayer(
            "ExtentLabels",
            QgsAnnotationLayer.LayerOptions(QgsProject.instance().transformContext()),
        )

        self.combo = QComboBox()
        self.combo.addItems(self.MAP_SOURCES.keys())
        self.combo.currentIndexChanged.connect(self.mapChanged)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(8, 64)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.setValue(self.chunks)

        self.slider.valueChanged.connect(self.sizeChanged)

        canvas = QgsMapCanvas()
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        canvas.setMinimumHeight(600)
        canvas.setExtent(canvas.fullExtent())

        self.canvas = canvas

        map_source = list(self.MAP_SOURCES.values())[0]
        map_name = list(self.MAP_SOURCES.keys())[0]
        self.osm_layer = QgsRasterLayer(map_source, map_name, "wms")
        if self.osm_layer.isValid():
            QgsProject.instance().addMapLayer(self.osm_layer, False)
            canvas.setLayers([self.anno_layer, self.osm_layer])
            canvas.setExtent(self.osm_layer.extent())

        layout.addWidget(self.combo)
        layout.addWidget(canvas)
        layout.addWidget(self.slider)

        self.preview_area = QgsAnnotationPolygonItem(QgsCurvePolygon())
        # Configure Style
        symbol = QgsFillSymbol.createSimple(
            {
                "color": "255,0,0,50",  # Semi-transparent red
                "outline_color": "red",
                "outline_width": "0",
            }
        )
        self.preview_area.setSymbol(symbol)
        self.preview_area.setZIndex(1)  # Bottom
        self.preview_area.setEnabled(False)
        self.anno_layer.addItem(self.preview_area)

        self.label = QgsAnnotationPointTextItem(None, QgsPointXY(0, 0))
        self.label.setZIndex(2)
        self.label.setEnabled(False)
        self.label.setAlignment(Qt.AlignCenter)
        self.item_id = self.anno_layer.addItem(self.label)

        fmt = QgsTextFormat()
        fmt.setSize(9)
        fmt.setAllowHtmlFormatting(True)
        # Optional: add a buffer/halo for visibility over OSM
        fmt.buffer().setEnabled(True)
        fmt.buffer().setSize(1)
        self.label.setFormat(fmt)

        self.marker_item = QgsAnnotationMarkerItem(QgsPoint(0, 0))

        # 2. Create a Cross Symbol
        # 'cross' is the standard X shape
        symbol = QgsMarkerSymbol.createSimple(
            {
                "name": "cross2",
                "outline_color": "red",
                "outline_width": "0.6",
                "size": "3",
            }
        )
        self.marker_item.setSymbol(symbol)
        self.marker_item.setZIndex(3)  # Ensure it is on top
        self.marker_item.setEnabled(False)
        self.anno_layer.addItem(self.marker_item)

        # canvas.extentsChanged.connect(self._update_rbs)

        self.clicker = CanvasClickTool(canvas)
        self.clicker.pointChanged.connect(self.pointChanged)
        canvas.setMapTool(self.clicker)

        return self.container

    def mapChanged(self, index: int):
        name = list(self.MAP_SOURCES.keys())[index]
        source = list(self.MAP_SOURCES.values())[index]
        self.osm_layer.setDataSource(source, name, "wms", False)
        self.osm_layer.triggerRepaint()

    def pointChanged(self, point: QgsPointXY):
        self.point = point
        self.update_preview()

    def sizeChanged(self, chunks: int):
        self.chunks = chunks
        self.update_preview()

    def setWidgetValue(self, value, is_changed=False):
        self.user_extent = value

    def widgetValue(self):
        return f"{self.user_extent.xMinimum()},{self.user_extent.xMaximum()},{self.user_extent.yMinimum()},{self.user_extent.yMaximum()} [{self.osm_layer.crs().authid()}]"

    def setWidgetContext(self, context):
        super().setWidgetContext(context)

    def update_preview(self):

        # Calculate user selected extent
        project_crs = self.osm_layer.crs()

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        to_wgs84 = QgsCoordinateTransform(project_crs, wgs84, QgsProject.instance())
        lat = to_wgs84.transform(self.point).y()

        user_size = ((self.chunks + 0.5) * 3072) * (1.0 / math.cos(math.radians(lat)))
        self.user_extent = QgsRectangle.fromCenterAndSize(
            self.point, user_size, user_size
        )

        isValidMapArea = not self.user_extent.isEmpty()

        self.marker_item.setEnabled(isValidMapArea)
        self.label.setEnabled(isValidMapArea)
        self.preview_area.setEnabled(isValidMapArea)

        # Bail out if user extent is empty
        if not isValidMapArea:
            return

        # Calculate map area and preview extent
        calculator = UtmMapAreaCalculator()
        map_extent, map_crs, chunks, map_size, corner_wgs84 = calculator.fromExtent(
            self.user_extent, project_crs
        )

        from_utm = QgsCoordinateTransform(map_crs, project_crs, QgsProject.instance())
        preview_extent = from_utm.transformBoundingBox(map_extent)
        self.preview_area.geometry().fromWkt(
            QgsGeometry.fromRect(preview_extent).asWkt()
        )

        # Update Label
        self.label.setText(
            f"<div><b>{(map_size / 1000):.0f} km ({chunks} Chunks)</b></div>"
            f"<div>{abs(corner_wgs84.y()):.5f}° {'N' if corner_wgs84.y() >= 0 else 'S'}</div>"
            f"<div>{abs(corner_wgs84.x()):.5f}° {'E' if corner_wgs84.x() >= 0 else 'W'}</div>"
            f"<div>{map_crs.description()}</div>"
        )
        self.label.setPoint(preview_extent.center())

        # Update click position
        self.marker_item.setGeometry(QgsPoint(self.point))

        self.anno_layer.triggerRepaint()

    def _cleanup(self):
        if hasattr(self, "canvas") and self.canvas:
            self.canvas.setMapTool(None)
            self.clicker = None
            self.canvas.deleteLater()
            self.canvas = None

        if hasattr(self, "osm_layer") and self.osm_layer:
            QgsProject.instance().removeMapLayer(self.osm_layer.id())
            self.osm_layer = None

    def __del__(self):
        self._cleanup()
