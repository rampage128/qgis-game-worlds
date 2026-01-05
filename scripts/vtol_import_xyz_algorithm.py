"""
XYZ IMPORTER:

This essentially does two things:

1. Build fake virtual raster based on XYZ source
2. download the extent from the virtual raster
3. calculate height values from RGB data
4. warp to target CRS

It simplifies the process of importing height data to be ready to use for flat mapping.

"""

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProject,
    QgsProcessingParameterEnum,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProcessingUtils,
    QgsProcessingParameterVectorLayer,
    QgsFeature,
)

from qgis.PyQt.QtCore import QVariant
from qgis import processing
from typing import Optional
import os
import math


class QgsProcessingParameterGeoTiffDestination(QgsProcessingParameterRasterDestination):
    def __init__(
        self,
        name,
        description="Output GeoTIFF file",
        defaultValue=None,
        optional=False,
        createByDefault=True,
    ):
        super().__init__(name, description, defaultValue, optional, createByDefault)

    def defaultFileExtension(self):
        return "tif"

    def supportedOutputRasterLayerExtensions(self):
        return ["tif", "tiff"]


class VtolImportXYZAlgorithm(QgsProcessingAlgorithm):

    PARAMETER_OUTPUT_RASTER = "OUTPUT"
    PARAMETER_RESAMPLING = "PARAMETER_RESAMPLING"
    PARAMETER_TARGET_CRS = "PARAMETER_TARGET_CRS"
    PARAMETER_SOURCE_XYZ = "PARAMETER_SOURCE_DEM"
    PARAMETER_TARGET_EXTENT = "PARAMETER_TARGET_EXTENT"
    PARAMETER_ZOOM = "PARAMETER_ZOOM"
    PARAMETER_SOURCE_NODATA = "PARAMETER_SOURCE_NODATA"
    PARAMETER_MAP_AREA = "PARAMETER_MAP_AREA"

    OPTIONS_RESAMPLING = {
        "nearest": "nearest",
        "bilinear": "bilinear",
        "cubic": "cubic",
        "cubicspline": "cubic spline",
        "lanczos": "lanczos",
        "average": "average",
        "mode": "mode",
    }
    OPTIONS_RESAMPLING_DEFAULT = list(OPTIONS_RESAMPLING.keys()).index("nearest")

    OPTION_NODATA_DEFAULT = -32768

    OPTION_ZOOM_DEFAULT = 12

    EARTH_CIRCUMFERENCE = 40075016.686
    XYZ_TILE_SIZE = 256

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PARAMETER_MAP_AREA,
                self.tr("<b>Map Information</b><br><br>Map Area"),
                [QVariant.Int, 3],  # QgsWkbTypes.Polygon
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PARAMETER_ZOOM,
                self.tr("<hr><br><b>Source Data</b><br><br>Zoom Level"),
                type=QgsProcessingParameterNumber.Integer,
                minValue=0,
                maxValue=15,
                defaultValue=self.OPTION_ZOOM_DEFAULT,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.PARAMETER_SOURCE_NODATA,
                self.tr("NODATA value"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=self.OPTION_NODATA_DEFAULT,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAMETER_RESAMPLING,
                self.tr("<hr><br><b>Output</b><br><br>Resampling Method"),
                list(self.OPTIONS_RESAMPLING.values()),
                defaultValue=self.OPTIONS_RESAMPLING_DEFAULT,
            )
        )

        self.addParameter(
            QgsProcessingParameterGeoTiffDestination(
                self.PARAMETER_OUTPUT_RASTER,
                self.tr("Output File"),
                createByDefault=True,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        map_area_layer = self.parameterAsVectorLayer(
            parameters, self.PARAMETER_MAP_AREA, context
        )
        source_nodata = self.parameterAsInt(
            parameters, self.PARAMETER_SOURCE_NODATA, context
        )
        zoom = self.parameterAsInt(parameters, self.PARAMETER_ZOOM, context)
        target_resampling = self.parameterAsEnum(
            parameters, self.PARAMETER_RESAMPLING, context
        )
        target_file = self.parameterAsFileOutput(
            parameters, self.PARAMETER_OUTPUT_RASTER, context
        )

        # 1. Validation
        if map_area_layer is None:
            raise QgsProcessingException(self.tr("Provided map area layer not found!"))

        feature_iterator = map_area_layer.getFeatures()
        area: Optional[QgsFeature] = next(feature_iterator, None)
        if area is None:
            raise QgsProcessingException(
                self.tr("No feature found in provided map area layer!")
            )

        feedback.setProgressText(self.tr(f"Importing XYZ (1/3): Extracting Tiles"))

        print(f"Original Vector Extent: {map_area_layer.extent().toString()}")
        print(f"Vector CRS: {map_area_layer.crs().authid()}")

        epsg_3857 = QgsCoordinateReferenceSystem("EPSG:3857")
        to_pseudo = QgsCoordinateTransform(
            map_area_layer.crs(), epsg_3857, QgsProject.instance()
        )
        map_area_geometry = QgsGeometry.fromRect(map_area_layer.extent())
        map_area_geometry.transform(to_pseudo)
        xyz_extent = map_area_geometry.boundingBox()

        epsg_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
        to_wgs_84 = QgsCoordinateTransform(
            map_area_layer.crs(), epsg_4326, QgsProject.instance()
        )
        lat = to_wgs_84.transform(map_area_layer.extent().center()).y()
        target_res = 153.6
        optimal_z = int(
            round(
                math.log2(
                    (self.EARTH_CIRCUMFERENCE * math.cos(math.radians(lat)))
                    / (256 * target_res)
                )
            )
        )

        feedback.pushInfo(self.tr(f"Detected ideal zoom level: {optimal_z}"))

        print(f"Transformed 3857 Extent: {xyz_extent.toString()}")
        print(f"PROJWIN Dimensions: {xyz_extent.width()}m x {xyz_extent.height()}m")

        cache_dir = QgsProcessingUtils.generateTempFilename("gdalwmscache")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        vrt_path = QgsProcessingUtils.generateTempFilename("mapzen.vrt", context)
        with open(vrt_path, "w") as f:
            f.write(
                (
                    "<GDAL_WMS>"
                    '<Service name="TMS">'
                    "<ServerUrl>https://s3.amazonaws.com/elevation-tiles-prod/terrarium/${z}/${x}/${y}.png</ServerUrl>"
                    "<UserAgent>Mozilla/5.0 (QGIS_K11_Facility)</UserAgent>"
                    "</Service>"
                    "<Cache>"
                    f"<Path>{cache_dir}</Path>"
                    "<Depth>2</Depth>"
                    "</Cache>"
                    "<ParallelThreads>16</ParallelThreads>"
                    "<DataWindow>"
                    "<UpperLeftX>-20037508.34</UpperLeftX>"
                    "<UpperLeftY>20037508.34</UpperLeftY>"
                    "<LowerRightX>20037508.34</LowerRightX>"
                    "<LowerRightY>-20037508.34</LowerRightY>"
                    f"<TileLevel>{zoom}</TileLevel>"
                    "<TileCountX>1</TileCountX>"
                    "<TileCountY>1</TileCountY>"
                    "<YOrigin>top</YOrigin>"
                    "</DataWindow>"
                    f"<Projection>{epsg_3857.authid()}</Projection>"
                    "<BlockSizeX>256</BlockSizeX>"
                    "<BlockSizeY>256</BlockSizeY>"
                    "<BandsCount>3</BandsCount>"
                    "</GDAL_WMS>"
                )
            )

        feedback.pushInfo(f"Created vrt file at: {vrt_path}")

        # map_units_per_pixel = self.EARTH_CIRCUMFERENCE / (
        #     self.XYZ_TILE_SIZE * (2**zoom)
        # )

        # 2. Extraction

        # This was faster, but it also requires to manually add a layer and basically screenshots the layer...
        # The result unfortunately had artifacts at color rollovers (banding rings on certain elevations)
        # extracted_source = processing.run(
        #     "native:rasterize",
        #     {
        #         "EXTENT": target_extent,
        #         "EXTENT_BUFFER": 0,
        #         "TILE_SIZE": self.XYZ_TILE_SIZE,
        #         "MAP_UNITS_PER_PIXEL": map_units_per_pixel,
        #         "MAKE_BACKGROUND_TRANSPARENT": False,
        #         "MAP_THEME": None,
        #         "LAYERS": [source_xyz],
        #         "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        #     },
        #     feedback=feedback,
        #     context=context,
        #     is_child_algorithm=True,
        # )["OUTPUT"]

        # For some reason warpreproject was really really slow with a vrt file
        # rgb_data = processing.run(
        #     "gdal:warpreproject",
        #     {
        #         "INPUT": vrt_path,
        #         "SOURCE_CRS": epsg_3857,
        #         "TARGET_CRS": map_area_layer.crs(),
        #         "RESAMPLING": 0,  # 0 is Nearest Neighbor
        #         "TARGET_RESOLUTION": target_res,  # Match your required precision in meters
        #         "OUTPUT_EXTENT": map_area_layer.extent(),
        #         "OPTIONS": "COMPRESS=LZW|TILED=YES",
        #         "DATA_TYPE": 0,  # Use 0 (Use Input Layer Data Type)
        #         "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        #         "EXTRA": "-multi -wo NUM_THREADS=ALL_CPUS -to ALLOW_BALLPARK=NO -to ONLY_BEST=YES",
        #     },
        #     context=context,
        #     feedback=feedback,
        #     is_child_algorithm=True,
        # )["OUTPUT"]

        gdal_config = (
            "--config GDAL_HTTP_MULTIPLEX YES "
            "--config GDAL_HTTP_VERSION 2 "
            "--config GDAL_HTTP_MAX_RETRY 3 "
            "--config GDAL_HTTP_TIMEOUT 10 "
            "--config GDAL_MAX_DATASET_POOL_SIZE 1024 "
            "--config VSI_CACHE TRUE "
            "--config VSI_CACHE_SIZE 536870912 "
            "--config GDAL_NUM_THREADS ALL_CPUS"
        )

        # So far the most stable option but pretty slow compared to the native:rasterize.
        rgb_data = processing.run(
            "gdal:translate",
            {
                "INPUT": vrt_path,
                "EXTRA": f"-projwin {xyz_extent.xMinimum()} {xyz_extent.yMaximum()} {xyz_extent.xMaximum()} {xyz_extent.yMinimum()} {gdal_config}",
                "RESAMPLING": 0,  # Nearest Neighbor - CRITICAL
                "DATA_TYPE": 1,
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        if feedback.isCanceled():
            return {}

        feedback.setProgressText(self.tr(f"Importing XYZ (2/3): Extracting Height"))

        height_data = processing.run(
            "gdal:rastercalculator",
            {
                # Formula to convert terrarium RGB into height values
                "FORMULA": "A*256 + B + C/256 - 32768",  # "-10000 + (A * (256**2) + (B * 256) + C) * 0.1",  # "(A * 256 + B + C/256) - 32768",
                "NO_DATA": source_nodata,
                # Float32
                "RTYPE": 5,
                # options
                "INPUT_A": rgb_data,
                "BAND_A": 1,
                "INPUT_B": rgb_data,
                "BAND_B": 2,
                "INPUT_C": rgb_data,
                "BAND_C": 3,
                "PROJWIN": xyz_extent,
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True,
        )["OUTPUT"]

        if feedback.isCanceled():
            return {}

        feedback.setProgressText(
            self.tr(f"Importing XYZ (3/3): Reprojecting Landscape")
        )

        # 3. Reprojection
        output = processing.run(
            "gdal:warpreproject",
            {
                "INPUT": height_data,
                "SOURCE_CRS": epsg_3857,
                "TARGET_CRS": map_area_layer,
                "RESAMPLING": target_resampling,
                "TARGET_RESOLUTION": None,
                "OPTIONS": "COMPRESS=DEFLATE|PREDICTOR=3|ZLEVEL=4",
                "DATA_TYPE": 6,
                "TARGET_EXTENT": None,
                "TARGET_EXTENT_CRS": None,
                "EXTRA": "-multi -wo NUM_THREADS=ALL_CPUS -to ALLOW_BALLPARK=NO -to ONLY_BEST=YES",
                "OUTPUT": target_file,
            },
            feedback=feedback,
            context=context,
            is_child_algorithm=True,
        )["OUTPUT"]

        if feedback.isCanceled():
            return {}

        context.addLayerToLoadOnCompletion(
            output,
            QgsProcessingContext.LayerDetails(
                f"{area["name"]} (xyz)", context.project()
            ),
        )

        return {self.PARAMETER_OUTPUT_RASTER: output}

    def name(self):
        return "vtolvr_xyz_import"

    def displayName(self):
        return self.tr("Import XYZ Data")

    def group(self):
        return self.tr("VTOL VR")

    def groupId(self):
        return "vtol_vr_maps"

    def tr(self, message):
        return message

    def createInstance(self):
        return VtolImportXYZAlgorithm()

    def shortHelpString(self) -> str:
        return (
            "Imports height data from Terrarium XYZ tiles. "
            "That means that you will not need to download any DEM files manually or make any accounts anywhere."
            "The output is returned as a new heightmap layer."
            "<div><b>NOTE: Your mileage with this may vary... The data is of very bad quality in a lot of areas.</b></div>"
            "<h3>Parameters</h3>"
            "<h4>Map Information</h4>"
            "<ul>"
            '<li><b>Map Area:</b> Select the map area layer you have created with "Create Map Area" before.</li>'
            "</ul>"
            "<h4>Source Data</h4>"
            "<ul>"
            "<li><b>Zoom Level:</b> If you have shorelines 12 is recommended. Anything lower may cause issues. 12 will already take very long, selecting anything higher is probably overkill.</li>"
            "<li><b>NODATA value:</b> Default of -32768 should be fine.</li>"
            "</ul>"
            "<h4>Output</h4>"
            "<ul>"
            '<li><b>Resampling Method:</b> Smoothes the image during reprojection. I would recommend sticking to "nearest" for this operation.</li>'
            "<li><b>Output File:</b> Path to where to store the final data.</li>"
            "</ul>"
            "After the Algorithm has completed the output will appear as a new layer. "
        )
