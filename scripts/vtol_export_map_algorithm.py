"""
VTOL VR MAP EXPORT

VTOL VR uses 4 8 bit PNGs to store map data.

The height data is stored in the R channel of each png file:

- height0.png: -80 - 1440
- height1.png: 1440 - 2960
- height2.png: 2960 - 4480
- height3.png: 4480 - 6000

That yields a total number of 1021 discrete height steps for each bit value.
That is 5.9607843137 meters/step vertical resolution.
Each image covers 1520m of altitude with 255 discrete steps.

Note that this Altitude range does not reflect ingame ASL.
The MSL ingame is at -5, which complicates the math and terrain rules.

City data is stored in the G channel of those images.

"""

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingOutputFolder,
    QgsRectangle,
    QgsVectorLayer,
    QgsCoordinateReferenceSystem,
    QgsProcessingParameterEnum,
    QgsAggregateCalculator,
)

from qgis.PyQt.QtCore import QVariant
from typing import Optional, TypedDict, Any, Literal
from qgis import processing

from pathlib import Path


class MapAreaInfo(TypedDict):
    chunks: int
    crs: QgsCoordinateReferenceSystem
    extent: QgsRectangle
    biome: str
    edge: str
    coast: str
    latitude: float
    longitude: float
    size: int


CityKey = Literal["RURAL", "SUBURB", "MIDTOWN", "DOWNTOWN_1", "DOWNTOWN_2"]


class VtolExportMapAlgorithmV2(QgsProcessingAlgorithm):

    PARAMETER_HEIGHT_SOURCE_LAYER = "HEIGHT_SOURCE_LAYER"
    PARAMETER_CLIPPING_LAYER = "CLIPPING_LAYER"
    PARAMETER_SOURCE_SEA_LEVEL = "SEA_LEVEL"
    PARAMETER_OUTPUT_FOLDER = "OUTPUT_FOLDER"
    PARAMETER_WATER_FALLOFF = "WATER_FALLOFF"
    PARAMETER_MAP_BIOME = "PARAMETER_MAP_BIOME"
    PARAMETER_MAP_EDGE = "PARAMETER_MAP_EDGE"
    PARAMETER_MAP_COAST = "PARAMETER_MAP_COAST"
    PARAMETER_RESAMPLING = "PARAMETER_RESAMPLING"
    PARAMETER_CITIES = "PARAMETER_CITIES"
    OUTPUT_FOLDER = "FOLDER"
    PARAMETER_INCLUDE_COMPOSITION_FILES = "DEBUG_COMPOSITION"

    OPTIONS_RESAMPLING = {
        "nearest": "nearest (blocky coasts, rough sharp peaks)",
        "bilinear": "bilinear (gently blurred coasts, softened slopes)",
        "cubic": "cubic (smoothly curved coasts, peaks with mild halos)",
        "cubicspline": "cubic spline (very smooth coasts, very smooth peaks)",
        "lanczos": "lanczos (sharp coasts, sharp peaks, some artifacts)",
        "average": "average (soft blurred coasts, flattened terrain)",
        "mode": "mode (stepped chunky coasts, plateaued peaks)",
        "maximum": "Maximum (precise peaks, muted valleys)",
        "minimum": "Minimum (muted peaks, precise valleys)",
        "median": "Median (what ever the fuck this is)",
        "q1": "First Quartile (soft version of Minimum)",
        "q3": "Third Quartile (soft version of Maximum)",
    }
    OPTIONS_RESAMPLING_DEFAULT = list(OPTIONS_RESAMPLING.keys()).index("q3")

    MAP_BIOME_OPTIONS = ["(From Map Area)", "Boreal", "Desert", "Arctic"]
    MAP_EDGE_OPTIONS = ["(From Map Area)", "Water", "Hills", "Coast"]
    MAP_COAST_OPTIONS = ["(From Map Area)", "North", "South", "East", "West"]

    CITY_TYPE_BURNS = {
        "RURAL": [51, 205, 0, 0, 0],
        "SUBURB": [99, 255, 143, 0, 0],
        "MIDTOWN": [149, 255, 255, 87, 0],
        "DOWNTOWN_1": [199, 255, 255, 255, 31],
        "DOWNTOWN_2": [249, 255, 255, 255, 229],
    }

    CITY_COLOR_MAP = {
        51: [205, 0, 0, 0],
        99: [255, 143, 0, 0],
        149: [255, 255, 87, 0],
        199: [255, 255, 255, 31],
        249: [255, 255, 255, 229],
    }

    CHUNK2PX = 20

    ALTITUDE_RESOLUTION = 1520.0 / 255.0
    HORIZONTAL_RESOLUTION = 153.6

    def _parse_area(self, parameters, context) -> MapAreaInfo:
        clipping_layer = self.parameterAsVectorLayer(
            parameters, self.PARAMETER_CLIPPING_LAYER, context
        )
        if clipping_layer is None:
            raise QgsProcessingException(self.tr("Provided map area layer not found!"))

        feature_iterator = clipping_layer.getFeatures()
        area: Optional[QgsFeature] = next(feature_iterator, None)
        if area is None:
            raise QgsProcessingException(
                self.tr("No feature found in provided map area layer!")
            )

        biome_index = self.parameterAsEnum(
            parameters, self.PARAMETER_MAP_BIOME, context
        )
        edge_index = self.parameterAsEnum(parameters, self.PARAMETER_MAP_EDGE, context)
        coast_index = self.parameterAsEnum(
            parameters, self.PARAMETER_MAP_COAST, context
        )

        biome = (
            area["biome"] if biome_index == 0 else self.MAP_BIOME_OPTIONS[biome_index]
        )
        edge = area["edge"] if edge_index == 0 else self.MAP_EDGE_OPTIONS[edge_index]
        coast = (
            area["coast"] if coast_index == 0 else self.MAP_COAST_OPTIONS[coast_index]
        )

        try:
            return {
                "biome": biome,
                "chunks": area["chunks"],
                "coast": coast,
                "edge": edge,
                "crs": clipping_layer.crs(),
                "extent": clipping_layer.extent(),
                "latitude": area["latitude"],
                "longitude": area["longitude"],
                "size": area["size"],
            }
        except KeyError:
            raise QgsProcessingException(
                self.tr("Required feature fields not found. Map area layer is invalid.")
            )

    def _write_vtm(
        self,
        map_area: MapAreaInfo,
        output_folder_path: Path,
    ):
        vtm_file_name = f"{output_folder_path.name}.vtm"
        vtm_file_path = output_folder_path / vtm_file_name

        map_edge = map_area["edge"]

        coast_side = (
            "" if map_edge != "Coast" else f"	coastSide = {map_area['coast']}\n"
        )

        data = (
            f"VTMapCustom\n"
            f"{{\n"
            f"	mapID = {output_folder_path.name}\n"
            f"	mapName = \n"
            f"	mapDescription = \n"
            f"	mapType = HeightMap\n"
            f"	edgeMode = {map_edge}\n"
            f"	longitude = {map_area['latitude']}\n"  # Game has lat and lon switched *lol*
            f"	latitude = {map_area['longitude']}\n"
            f"	cloudHeightOffset = -1\n"
            f"{coast_side}"
            f"	biome = {map_area['biome']}\n"
            f"	seed = seed\n"
            f"	mapSize = {map_area['chunks']}\n"
            f"	TerrainSettings\n"
            f"	{{\n"
            f"	}}\n"
            f"}}\n"
        )

        vtm_file_path.write_text(data, encoding="utf-8", newline="\n")

    def _create_height(
        self,
        output_folder_path: Path,
        map_area: MapAreaInfo,
        parameters: dict[str, Any],
        feedback: QgsProcessingFeedback,
        context: QgsProcessingContext,
    ):
        temporary = not self.parameterAsBoolean(
            parameters, self.PARAMETER_INCLUDE_COMPOSITION_FILES, context
        )

        clipped_target_path = (
            QgsProcessing.TEMPORARY_OUTPUT
            if temporary
            else str(output_folder_path / "01_clipped_target.tif")
        )
        water_mask_path = (
            QgsProcessing.TEMPORARY_OUTPUT
            if temporary
            else str(output_folder_path / "02_water_mask.tif")
        )
        water_depth_map_path = (
            QgsProcessing.TEMPORARY_OUTPUT
            if temporary
            else str(output_folder_path / "03_water_depth.tif")
        )
        blended_terrain_path = (
            QgsProcessing.TEMPORARY_OUTPUT
            if temporary
            else str(output_folder_path / "04_blended_terrain.tif")
        )

        sea_level = self.parameterAsDouble(
            parameters, self.PARAMETER_SOURCE_SEA_LEVEL, context
        )

        water_falloff_distance = self.parameterAsDouble(
            parameters, self.PARAMETER_WATER_FALLOFF, context
        )

        feedback.setProgressText(
            self.tr("Generating Heightmap (1/4): Clipping DEM Data")
        )

        source_layer = self.parameterAsRasterLayer(
            parameters, self.PARAMETER_HEIGHT_SOURCE_LAYER, context
        )

        target_width = (map_area["chunks"] * self.CHUNK2PX) + 1
        resampling_index = self.parameterAsEnum(
            parameters, self.PARAMETER_RESAMPLING, context
        )

        clipped_target = processing.run(
            "gdal:warpreproject",
            {
                "INPUT": source_layer,
                "SOURCE_CRS": source_layer,
                "TARGET_CRS": map_area["crs"],
                "RESAMPLING": resampling_index,
                "NODATA": None,
                "OPTIONS": "COMPRESS=DEFLATE|PREDICTOR=2|ZLEVEL=9",
                "DATA_TYPE": 6,  # use source format
                "TARGET_EXTENT": map_area["extent"],
                "TARGET_EXTENT_CRS": map_area["crs"],
                "EXTRA": f"-ts {target_width} {target_width} -ovr NONE -wt Float32 -multi -wo NUM_THREADS=ALL_CPUS -to ALLOW_BALLPARK=NO -to ONLY_BEST=YES",
                "OUTPUT": clipped_target_path,  # path to a tiff file or temporary
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        feedback.setProgressText(
            self.tr("Generating Heightmap (2/4): Detecting Water Surface")
        )

        water_mask_sea_level = sea_level + 0.5  # +2.4902

        water_mask = processing.run(
            "gdal:rastercalculator",
            {
                # sets everything above sea_level to 255 (and everything else to 0)
                "FORMULA": f"(A > ({water_mask_sea_level})) * 255",
                # uint16 to prevent issues during blending stage
                "RTYPE": 2,
                # options
                "INPUT_A": clipped_target,
                "BAND_A": 1,
                "OUTPUT": water_mask_path,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        feedback.setProgressText(
            self.tr("Generating Heightmap (3/4): Simulating Water Depth")
        )

        max_water_falloff = round(water_falloff_distance / self.HORIZONTAL_RESOLUTION)

        water_depth_map = processing.run(
            "gdal:proximity",
            {
                # creates a gradient from 0 at shoreline to water_falloff_distance in meters
                "UNITS": 1,
                "MAX_DISTANCE": max_water_falloff,
                # fills areas exceeding MAX_DISTANCE with the fixed max value (NODATA)
                "NODATA": max_water_falloff,
                # uint16 allows up to ~65km falloff
                "DATA_TYPE": 2,
                # options
                "INPUT": water_mask,
                "BAND": 1,
                "OUTPUT": water_depth_map_path,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        feedback.setProgressText(
            self.tr("Generating Heightmap (4/4): Blending Terrain and Water")
        )

        water_scale_factor = 12 / (max_water_falloff - 1.0)
        water_formula = f"round(12 - ((B - 1.0) * {water_scale_factor}))"

        # land_formula = f"round(((A + 80.0) / {self.ALTITUDE_RESOLUTION}) + 12 - (({sea_level} + 80.0) / {self.ALTITUDE_RESOLUTION}))"
        land_formula = f"floor(((maximum(A, {sea_level}) - {sea_level}) / {self.ALTITUDE_RESOLUTION}) + 12.58)"

        blended_terrain = processing.run(
            "gdal:rastercalculator",
            {
                "RTYPE": 2,  # uint16
                "FORMULA": f"(C > 0) * {land_formula} + (C == 0) * {water_formula}",
                "INPUT_A": clipped_target,
                "BAND_A": 1,
                "INPUT_B": water_depth_map,
                "BAND_B": 1,
                "INPUT_C": water_mask,
                "BAND_C": 1,
                "OUTPUT": blended_terrain_path,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        return blended_terrain

    # TODO: Combine this with _write_height...
    def _write_height_x(
        self,
        output_folder_path: Path,
        height_map: str,
        city_layer: str | None,
        index: int,
        parameters: dict[str, Any],
        feedback: QgsProcessingFeedback,
        context: QgsProcessingContext,
    ):
        temporary = not self.parameterAsBoolean(
            parameters, self.PARAMETER_INCLUDE_COMPOSITION_FILES, context
        )

        merged_height_path = (
            QgsProcessing.TEMPORARY_OUTPUT
            if temporary
            else str(output_folder_path / f"06_height{index}_merged.tif")
        )
        output_path = str(output_folder_path / f"height{index}.png")

        offset = index * 255

        # TODO: Remove cities on water... we can do this by adding A >= 13
        city_colors = list(self.CITY_TYPE_BURNS.values())
        city_terms = []
        for city_index, city_level_colors in enumerate(city_colors):
            color_index = index + 1
            city_index_value = city_index + 1
            term = f"((B == {city_index_value}) * {city_level_colors[color_index]})"
            city_terms.append(term)

        city_term = "0" if city_layer is None else " + ".join(city_terms)

        merged_height = processing.run(
            "gdal:rastercalculator",
            {
                "RTYPE": 0,  # byte
                "FORMULA": f"(A >= {offset}) * (A - {offset})",
                "EXTRA": f'--calc "{city_term}" --calc "0" --calc "255"',
                "INPUT_A": height_map,
                "BAND_A": 1,
                "INPUT_B": city_layer,
                "BAND_B": 1,
                "OUTPUT": merged_height_path,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        processing.run(
            "gdal:translate",
            {
                "INPUT": merged_height,
                "OUTPUT": output_path,
                "NODATA": None,
                "DATA_TYPE": 1,
                "OVERWRITE": True,
                "TARGET_CRS": None,
                "EXTRA": "-b 1 -b 2 -b 3 -b 4 -colorinterp red,green,blue,alpha --config GDAL_PAM_ENABLED NO",
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

    def _write_height(
        self,
        output_folder_path: Path,
        height_map: str,
        city_layer: str | None,
        parameters: dict[str, Any],
        feedback: QgsProcessingFeedback,
        context: QgsProcessingContext,
    ):
        temporary = not self.parameterAsBoolean(
            parameters, self.PARAMETER_INCLUDE_COMPOSITION_FILES, context
        )

        merged_height_path = (
            QgsProcessing.TEMPORARY_OUTPUT
            if temporary
            else str(output_folder_path / f"06_height_merged.tif")
        )
        output_path = str(output_folder_path / f"height.png")

        range = 255 / 1020

        city_colors = list(self.CITY_TYPE_BURNS.values())
        city_terms = []
        for city_index, city_level_colors in enumerate(city_colors):
            city_index_value = city_index + 1
            term = f"((B == {city_index_value}) * {city_level_colors[0]})"
            city_terms.append(term)

        city_term = "0" if city_layer is None else " + ".join(city_terms)

        merged_height = processing.run(
            "gdal:rastercalculator",
            {
                "RTYPE": 0,  # uint16
                "FORMULA": f"round(A * {range})",
                "EXTRA": f'--calc "{city_term}" --calc "0" --calc "255"',
                "INPUT_A": height_map,
                "BAND_A": 1,
                "INPUT_B": city_layer,
                "BAND_B": 1,
                "OUTPUT": merged_height_path,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        processing.run(
            "gdal:translate",
            {
                "INPUT": merged_height,
                "OUTPUT": output_path,
                "NODATA": None,
                "DATA_TYPE": 1,
                "OVERWRITE": True,
                "TARGET_CRS": None,
                "EXTRA": "-b 1 -b 2 -b 3 -b 4 -colorinterp red,green,blue,alpha --config GDAL_PAM_ENABLED NO",
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )

    def _burn_cities(
        self,
        map_area: MapAreaInfo,
        city_layer: QgsVectorLayer,
        feedback: QgsProcessingFeedback,
        context: QgsProcessingContext,
    ):

        if city_layer.fields().indexFromName("City Level") == -1:
            raise QgsProcessingException(
                self.tr(
                    f'Selected city layer "{city_layer.name()}" is not a city layer.'
                )
            )

        # 1. Sort city shapes by city level. GDAL will use the attribute "fid" for ordering!
        sorted_cities = QgsVectorLayer(
            f"Polygon?crs={city_layer.crs().authid()}", "sorted_cities", "memory"
        )
        sorted_cities.dataProvider().addAttributes(city_layer.fields())
        sorted_cities.updateFields()
        sorted_features = sorted(
            city_layer.getFeatures(), key=lambda f: f["City Level"]
        )

        for index, feature in enumerate(sorted_features):
            new_feature = QgsFeature(index)
            new_feature.setFields(sorted_cities.fields())
            new_feature.setGeometry(feature.geometry())
            new_feature.setAttributes(feature.attributes())
            new_feature.setAttribute("fid", index)
            sorted_cities.dataProvider().addFeature(new_feature)

        # 2. Burn the cities into a raster
        target_width = (map_area["chunks"] * self.CHUNK2PX) + 1

        return processing.run(
            "gdal:rasterize",
            {
                "DATA_TYPE": 0,
                "INPUT": sorted_cities,
                "FIELD": "City Level",
                "UNITS": 0,
                "WIDTH": target_width,
                "HEIGHT": target_width,
                "EXTENT": map_area["extent"],
                "INIT": 0,
                "NODATA": 255,
                "PROJWIN_CRS": map_area["crs"],
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            feedback=feedback,
            context=context,
            is_child_algorithm=True,
        )["OUTPUT"]

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PARAMETER_CLIPPING_LAYER,
                self.tr("<b>Map Information</b><br><br>Map Area"),
                [QVariant.Int, 3],  # QgsWkbTypes.Polygon
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAMETER_MAP_BIOME,
                self.tr("Biome"),
                options=self.MAP_BIOME_OPTIONS,
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAMETER_MAP_EDGE,
                self.tr("Map Edge"),
                options=self.MAP_EDGE_OPTIONS,
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAMETER_MAP_COAST,
                self.tr("Map Coast Side (only used if Map Edge = Coast)"),
                options=self.MAP_COAST_OPTIONS,
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.PARAMETER_HEIGHT_SOURCE_LAYER,
                self.tr("<hr><br><b>Height Data</b><br><br>Digital Elevation Model"),
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PARAMETER_SOURCE_SEA_LEVEL,
                self.tr("Sea Level Altitude (meters)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PARAMETER_WATER_FALLOFF,
                self.tr("Water Falloff Distance (meters)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=2000,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAMETER_RESAMPLING,
                self.tr("Resampling (scaling) method"),
                options=list(self.OPTIONS_RESAMPLING.values()),
                defaultValue=self.OPTIONS_RESAMPLING_DEFAULT,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAMETER_INCLUDE_COMPOSITION_FILES,
                self.tr("Generate debug files"),
            )
        )

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PARAMETER_CITIES,
                self.tr("<hr><br><b>City Data</b><br><br>City Source"),
                [QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.PARAMETER_OUTPUT_FOLDER,
                self.tr("<hr><br><b>Output</b><br><br>Map Output Folder"),
                createByDefault=True,
            )
        )

        self.addOutput(
            QgsProcessingOutputFolder(self.OUTPUT_FOLDER, self.tr("Map Output Folder"))
        )

    def processAlgorithm(self, parameters, context, feedback):
        output_folder = self.parameterAsFileOutput(
            parameters, self.PARAMETER_OUTPUT_FOLDER, context
        )
        output_folder_path = Path(output_folder)
        output_folder_path.mkdir(parents=True, exist_ok=True)

        feedback.setProgressText(self.tr("Parsing map area"))
        map_area = self._parse_area(parameters, context)

        feedback.setProgressText(self.tr("Generating Heightmap"))
        height_map = self._create_height(
            output_folder_path, map_area, parameters, feedback, context
        )

        feedback.setProgressText(self.tr("Burning Cities"))

        city_layer = self.parameterAsVectorLayer(
            parameters, self.PARAMETER_CITIES, context
        )

        burned_cities = (
            None
            if city_layer is None
            else self._burn_cities(map_area, city_layer, feedback, context)
        )

        feedback.setProgressText(self.tr("Generating map images"))

        for index in range(4):
            feedback.setProgressText(
                self.tr(f"Generating map images ({index+1}/5): height{index}.png")
            )

            self._write_height_x(
                output_folder_path,
                height_map,
                burned_cities,
                index,
                parameters,
                feedback,
                context,
            )

        feedback.setProgressText(self.tr(f"Generating map images (5/5): height.png"))

        self._write_height(
            output_folder_path, height_map, burned_cities, parameters, feedback, context
        )

        feedback.setProgressText(self.tr("Generating vtm file"))
        self._write_vtm(map_area, output_folder_path)

        feedback.setProgressText(self.tr("Generating report"))

        population_report_html = ""
        population_report_text = ""
        if city_layer is not None:
            area_calculator = QgsAggregateCalculator(city_layer)
            area_calculator.calculate(Qgis.Aggregate.Sum, "area($geometry)")

            icons = {"html": ["&#x1F7E2;", "&#x1F534;"], "text": ["✓", "✗"]}

            areas = []
            states = []
            for i in range(1, 6):
                area_calculator.setFilter(f'"City Level" = {i}')
                area, _ = area_calculator.calculate(
                    Qgis.Aggregate.Sum, "area($geometry)"
                )
                area_km = 0 if area is None else round(area / 1000000, 1)
                areas.append(area_km)
                states.append(0 if area_km < 300 else 1)

            area_calculator.setFilter("")
            area, _ = area_calculator.calculate(Qgis.Aggregate.Sum, "area($geometry)")
            area_km = 0 if area is None else round(area / 1000000, 1)
            areas.append(area_km)
            states.append(0 if area_km < 400 else 1)

            population_report_html = (
                f"<h4>Population Report:</h4>"
                f"{icons["html"][states[0]]} Rural: {areas[0]}km²<br>"
                f"{icons["html"][states[1]]} Suburb: {areas[1]}km²<br>"
                f"{icons["html"][states[2]]} Midtown: {areas[2]}km²<br>"
                f"{icons["html"][states[3]]} Downtown I: {areas[3]}km²<br>"
                f"{icons["html"][states[4]]} Downtown II: {areas[4]}km²<br>"
                f"<b>{icons["html"][states[5]]} Total: {areas[5]}km²</b><br>"
            )

            population_report_text = (
                f"Population Report:\n"
                f"{icons["text"][states[0]]} Rural: {areas[0]}km²\n"
                f"{icons["text"][states[1]]} Suburb: {areas[1]}km²\n"
                f"{icons["text"][states[2]]} Midtown: {areas[2]}km²\n"
                f"{icons["text"][states[3]]} Downtown I: {areas[3]}km²\n"
                f"{icons["text"][states[4]]} Downtown II: {areas[4]}km²\n"
                f"{icons["text"][states[5]]} Total: {areas[5]}km²\n",
            )

        feedback.pushFormattedMessage(
            f"<hr>" f"<h3>&#x1F4CA; Statistics</h3>" f"{population_report_html}",
            f"Statistics\n" f"--------------\n\n" f"{population_report_text}",
        )

        return {self.OUTPUT_FOLDER: output_folder}

    def name(self):
        return "vtolvr_map_creator"

    def displayName(self):
        return self.tr("Export map area")

    def group(self):
        return self.tr("VTOL VR")

    def groupId(self):
        return "vtol_vr_maps"

    def tr(self, message):
        return message

    def createInstance(self):
        return VtolExportMapAlgorithmV2()

    def shortHelpString(self) -> str:
        return (
            "Exports a selected map area as final VTOL VR map.<br><br>"
            "The output contains all necessary files (height.png, height0.png, height1.png, height2.png, height3.png and vtm-file).<br><br>"
            "You at least need to have a height data layer and a map area layer to run this."
            "<h3>Parameters</h3>"
            "<h4>Map Information</h4>"
            "<ul>"
            "<li><b>Map Area:</b> Select the map area you want to export.</li>"
            "<li><b>Biome, Edge, Coast Side:</b> Default takes the values defined in the map area, override here if you want to test different settings.</li>"
            "</ul>"
            "<h4>Height Data</h4>"
            "<ul>"
            "<li><b>Digital Elevation Model:</b> Select your height data layer here.</li>"
            "<li><b>Sea Level Altitude:</b> Allows you to change the sea level to adjust up or down.</li>"
            "<li><b>Water Falloff Distance:</b> All water will be filled with a smooth gradient around the coast. This defines how large the gradient should be in meters.</li>"
            '<li><b>Resampling Method:</b> Smoothes the image during rescaling. Hard to give a good advice on what to pick, but "Third Quartile" and "Maximum" are a solid choice.</li>'
            "<li><b>Generate debug files:</b> This will generate all the images of the intermediate steps for debugging. Those images can be loaded into QGIS and used to compare with the source data.</li>"
            "</ul>"
            "<h4>City Data</h4>"
            'An optional vector layer containing city shapes. Created with the "Create city zones" algorithm.'
            "<h4>Output</h4>"
            "Select the folder to export to (you can create a folder in CustomMaps and directly export into that folder).<br><br>"
            "<b>IMPORTANT:</b> The name of the folder will be the map id!"
        )
