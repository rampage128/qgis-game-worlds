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
    PARAMETER_SHORELINE_BIAS = "PARAMETER_SHORELINE_BIAS"
    PARAMETER_MAP_BIOME = "PARAMETER_MAP_BIOME"
    PARAMETER_MAP_EDGE = "PARAMETER_MAP_EDGE"
    PARAMETER_MAP_COAST = "PARAMETER_MAP_COAST"
    PARAMETER_RESAMPLING = "PARAMETER_RESAMPLING"
    PARAMETER_CITIES = "PARAMETER_CITIES"
    OUTPUT_FOLDER = "FOLDER"
    PARAMETER_INCLUDE_COMPOSITION_FILES = "DEBUG_COMPOSITION"

    OPTIONS_RESAMPLING = {
        "nearest": "Nearest (no resampling, noisy)",
        "bilinear": "Bilinear (smoothed hills and valleys)",
        "cubic": "Cubic (smoother hills and valleys)",
        "cubicspline": "Cubic spline (very smooth hills and valleys)",
        "lanczos": "Lanczos (maximum detail, punchy slopes)",
        "average": "Average (muted/flattened terrain)",
        "mode": "Mode (stepped terrain)",
        "maximum": "Maximum (preserve peaks and ridges)",
        "minimum": "Minimum (preserve dips and valleys)",
        "median": "Median (clean slopes, filters noise)",
        "q1": "First Quartile (slight focus on peaks and ridges)",
        "q3": "Third Quartile (slight focus on dips and valleys)",
    }
    OPTIONS_RESAMPLING_DEFAULT = list(OPTIONS_RESAMPLING.keys()).index("lanczos")

    MAP_BIOME_OPTIONS = ["(From Map Area)", "Boreal", "Desert", "Arctic"]
    MAP_EDGE_OPTIONS = ["(From Map Area)", "Water", "Hills", "Coast"]
    MAP_COAST_OPTIONS = ["(From Map Area)", "North", "South", "East", "West"]

    SHORELINE_BIAS_OPTIONS = [
        "Maximum Land Details (Preserves all land details)",
        "More Land Details (more focus on narrow land strips)",
        "Balanced",
        "More Water Details (more focus on narrow water strips)",
        "Maximum Water Details (preserves all water details)",
    ]
    SHORELINE_BIAS_VALUES = [0.2, 0.5, 1.0, 2.5, 5.0]
    SHORELINE_BIAS_DEFAULT = SHORELINE_BIAS_OPTIONS.index("Balanced")

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

        def path(name):
            return (
                QgsProcessing.TEMPORARY_OUTPUT
                if temporary
                else str(output_folder_path / f"{name}.tif")
            )

        hires_source = path("01_hires_source")
        hires_terrain = path("02_hires_terrain")
        hires_slope = path("03_hires_slope")
        hires_mask = path("04_hires_mask")
        hires_shore_mask = path("05_hires_shore")
        hires_shore = path("06_hires_shore")

        lores_terrain = path("07_lores_terrain")
        lores_mask_weight = path("08_lores_mask_weight")
        lores_shore = path("9_lores_shore")
        lores_bathy_gradient = path("10_lores_bathy_gradient")
        lores_bathy_slope = path("11_lores_bathy_slope")
        output = path("12_lores_blend")

        float_compression = "COMPRESS=DEFLATE|PREDICTOR=3|ZLEVEL=6"
        int_compression = "COMPRESS=DEFLATE|PREDICTOR=2|ZLEVEL=6"

        sea_level = self.parameterAsDouble(
            parameters, self.PARAMETER_SOURCE_SEA_LEVEL, context
        )

        water_retention_index = self.parameterAsEnum(
            parameters, self.PARAMETER_SHORELINE_BIAS, context
        )
        water_retention = self.SHORELINE_BIAS_VALUES[water_retention_index]

        source_layer = self.parameterAsRasterLayer(
            parameters, self.PARAMETER_HEIGHT_SOURCE_LAYER, context
        )

        target_width = (map_area["chunks"] * self.CHUNK2PX) + 1
        resampling_index = self.parameterAsEnum(
            parameters, self.PARAMETER_RESAMPLING, context
        )

        height_step = 5.9607843137
        pixel_size = 153.6

        # 1. REPROJECT SOURCE:
        feedback.setProgressText("Generating Heightmap (1/12): Clipping DEM Data")
        hires_source = processing.run(
            "gdal:warpreproject",
            {
                "INPUT": source_layer,
                "SOURCE_CRS": source_layer,
                "TARGET_CRS": map_area["crs"],
                "RESAMPLING": 0,
                "NODATA": None,
                "OPTIONS": float_compression,
                "DATA_TYPE": 6,  # Float32
                "TARGET_EXTENT": map_area["extent"],
                "TARGET_EXTENT_CRS": map_area["crs"],
                "EXTRA": f"-ovr NONE -wt Float32 -multi -wo NUM_THREADS=ALL_CPUS -to ALLOW_BALLPARK=NO -to ONLY_BEST=YES",
                "OUTPUT": hires_source,  # path to a tiff file or temporary
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        # 2. EXTRACT TERRAIN, SHIFT TO USER SEA LEVEL AND CUT OFF <= 0 TERRAIN
        feedback.setProgressText("Generating Heightmap (2/12): Extracting Terrain")
        hires_terrain = processing.run(
            "gdal:rastercalculator",
            {
                # sets everything above sea_level to 255 (and everything else to 0)
                "FORMULA": f"((A - {sea_level}) > 0) * (A - {sea_level})",
                # Float 32 for highest accuracy
                "RTYPE": 5,
                # options
                "INPUT_A": hires_source,
                "BAND_A": 1,
                "OUTPUT": hires_terrain,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        # 3. CAPTURE SOURCE TERRAIN SLOPES USED TO INFER BATHYMETRY SLOPES
        feedback.setProgressText("Generating Heightmap (3/12): Calculating Slopes")
        hires_slope = processing.run(
            "gdal:slope",
            {"INPUT": hires_source, "AS_PERCENT": True, "OUTPUT": hires_slope},
            context=context,
            feedback=feedback,
        )["OUTPUT"]

        # 4. CAPTURE HIRES WATER MASK FOR SHORELINE PRECISION
        feedback.setProgressText("Generating Heightmap (4/12): Detecting Shorelines")
        hires_mask = processing.run(
            "gdal:rastercalculator",
            {
                "INPUT_A": hires_terrain,
                "BAND_A": 1,
                # Byte
                "RTYPE": 0,
                "FORMULA": "A > 0",
                "OUTPUT": hires_mask,
            },
            context=context,
            feedback=feedback,
        )["OUTPUT"]

        # 5. CREATE HIRES SHORELINE MASK OF ~3PX width
        feedback.setProgressText("Generating Heightmap (5/12): Masking Shorelines")
        hires_shore_mask = processing.run(
            "gdal:proximity",
            {
                "INPUT": hires_mask,
                "VALUES": "0",
                "UNITS": 1,
                "REPLACE": 1,
                "MAX_DISTANCE": 3,
                "OUTPUT": hires_shore_mask,
                "NODATA": 0,
            },
            context=context,
            feedback=feedback,
        )["OUTPUT"]

        # 6. CREATE HIRES SHORELINE SEED
        feedback.setProgressText(
            "Generating Heightmap (6/12): Calculating shore steepness"
        )
        hires_shore = processing.run(
            "gdal:rastercalculator",
            {
                "INPUT_A": hires_shore_mask,
                "BAND_A": 1,
                "INPUT_B": hires_slope,
                "BAND_B": 1,
                "FORMULA": "where(A > 0, B, -9999)",
                "NO_DATA": -9999,
                "OUTPUT": hires_shore,
            },
            context=context,
            feedback=feedback,
        )["OUTPUT"]

        # 7. DOWNSAMPLING
        feedback.setProgressText(
            f"Generating Heightmap (7/12): Resampling terrain using {list(self.OPTIONS_RESAMPLING.keys())[resampling_index]}"
        )
        lores_terrain = processing.run(
            "gdal:warpreproject",
            {
                "INPUT": hires_terrain,
                "RESAMPLING": resampling_index,
                "NODATA": None,
                "DATA_TYPE": 6,  # Float32
                "OPTIONS": float_compression,
                "EXTRA": f"-ts {target_width} {target_width} -ovr NONE -wt Float32 -multi -wo NUM_THREADS=ALL_CPUS -to ALLOW_BALLPARK=NO -to ONLY_BEST=YES",
                "OUTPUT": lores_terrain,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        feedback.setProgressText("Generating Heightmap (8/12): Resampling shorelines")
        lores_mask_weight = processing.run(
            "gdal:warpreproject",
            {
                "INPUT": hires_mask,
                "RESAMPLING": list(self.OPTIONS_RESAMPLING.keys()).index("average"),
                "NODATA": None,
                "DATA_TYPE": 6,  # Float32
                "OPTIONS": int_compression,
                "EXTRA": f"-ts {target_width} {target_width} -ovr NONE -multi -wo NUM_THREADS=ALL_CPUS -to ALLOW_BALLPARK=NO -to ONLY_BEST=YES",
                "OUTPUT": lores_mask_weight,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        feedback.setProgressText(
            "Generating Heightmap (9/12): Resampling shoreline steepness"
        )
        lores_shore = processing.run(
            "gdal:warpreproject",
            {
                "INPUT": hires_shore,
                "RESAMPLING": list(self.OPTIONS_RESAMPLING.keys()).index("maximum"),
                "NODATA": None,
                "DATA_TYPE": 6,  # Float32
                "OPTIONS": float_compression,
                "EXTRA": f"-ts {target_width} {target_width} -ovr NONE -wt Float32 -multi -wo NUM_THREADS=ALL_CPUS -to ALLOW_BALLPARK=NO -to ONLY_BEST=YES",
                "OUTPUT": lores_shore,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        # 8. CREATE BATHYMETRY SLOPES FROM SHORE SEED
        feedback.setProgressText(
            "Generating Heightmap (10/12): Generating bathymetry slopes"
        )
        lores_bathy_slope = processing.run(
            "gdal:fillnodata",
            {
                "INPUT": lores_shore,
                "DISTANCE": 20,
                "OUTPUT": lores_bathy_slope,
                "ITERATIONS": 3,
            },
            context=context,
            feedback=feedback,
        )["OUTPUT"]

        # 9. CREATE BATHYMETRY GRADIENT (this is the shallowest possible non-banding slope for 12 height levels)
        feedback.setProgressText(
            "Generating Heightmap (11/12): Generating bathymetry gradient"
        )
        max_bathy_distance = pixel_size * 13
        lores_bathy_gradient = processing.run(
            "gdal:proximity",
            {
                "INPUT": lores_mask_weight,
                "VALUES": "1",
                "UNITS": 0,
                "OUTPUT": lores_bathy_gradient,
                "MAX_DISTANCE": max_bathy_distance,
                "NODATA": max_bathy_distance,
            },
            context=context,
            feedback=feedback,
        )["OUTPUT"]

        # 10. BLEND EVERYTHING AND QUANTIZE INTO FINAL IMAGE... (AND PRAY IT LOOKS GOOD)
        feedback.setProgressText(
            "Generating Heightmap (12/12): Blending final heightmap"
        )
        zero_offset = 80 - 2.5098039219

        interpolation_weight = f"pow(B, {water_retention})"
        bathy_formula = (
            f"maximum(-80, minimum(-(C * (D / 100.0)), "
            f"-(C / {pixel_size} * {height_step})))"
        )
        interpolation_formula = f"((A * {interpolation_weight}) + ({bathy_formula} * (1 - {interpolation_weight})))"

        output = processing.run(
            "gdal:rastercalculator",
            {
                "INPUT_A": lores_terrain,
                "BAND_A": 1,
                "INPUT_B": lores_mask_weight,
                "BAND_B": 1,
                "INPUT_C": lores_bathy_gradient,
                "BAND_C": 1,
                "INPUT_D": lores_bathy_slope,
                "BAND_D": 1,
                "OUTPUT": output,
                "EXTRA": "--hideNoData",
                "FORMULA": (
                    f"round(maximum(0, minimum({interpolation_formula} + {zero_offset}, 6080)) "
                    f"* (1 / {height_step}))"
                ),
            },
            context=context,
            feedback=feedback,
        )["OUTPUT"]

        return output

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
            else str(output_folder_path / f"13_height{index}_merged.tif")
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
            else str(output_folder_path / f"13_height_merged.tif")
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
            QgsProcessingParameterEnum(
                self.PARAMETER_SHORELINE_BIAS,
                self.tr("Shoreline Detail Priority"),
                options=self.SHORELINE_BIAS_OPTIONS,
                defaultValue=self.SHORELINE_BIAS_DEFAULT,
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
            "<li><b>Shoreline Detail Priority:</b> Decides wether to preserve water or land features that are smaller than the terrain resolution.</li>"
            "<li><b>Resampling Method:</b> Smoothes the image during rescaling. Pick what fits your terrain best. This does not affect the shorelines.</li>"
            "<li><b>Generate debug files:</b> This will generate all the images of the intermediate steps for debugging. Those images can be loaded into QGIS and used to compare with the source data.</li>"
            "</ul>"
            "<h4>City Data</h4>"
            'An optional vector layer containing city shapes. Created with the "Create city zones" algorithm.'
            "<h4>Output</h4>"
            "Select the folder to export to (you can create a folder in CustomMaps and directly export into that folder).<br><br>"
            "<b>IMPORTANT:</b> The name of the folder will be the map id!"
        )
