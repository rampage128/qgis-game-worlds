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
    QgsFeature,
    QgsProcessingContext,
)
from qgis.PyQt.QtCore import QMetaType, QVariant
from qgis.PyQt.QtGui import QColor
from typing import cast, Optional


class VtolCreateCitiesAlgorithm(QgsProcessingAlgorithm):
    OUTPUT = "OUTPUT"
    PARAMETER_MAP_AREA = "MAP_AREA"

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

        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT, self.tr("<hr><br><b>Output</b><br><br>City Zones")
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
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

        context.addLayerToLoadOnCompletion(
            dest_id,
            QgsProcessingContext.LayerDetails(
                f"{area["name"]} (cities)", context.project()
            ),
        )

        return {self.OUTPUT: dest_id}

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
            'For a more elaborate documentation on QGIS vector editing see: <a href="https://docs.qgis.org/3.40/en/docs/user_manual/working_with_vector/editing_geometry_attributes.html#digitizing-an-existing-layer">QGIS layer digitizing.</a>.'
        )
