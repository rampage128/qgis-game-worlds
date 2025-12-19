"""
DEN IMPORTER:

This essentially does two things:

1. Build virtual raster based on selected files
2. Warp to target CRS

It simplifies the process of importing height data to be ready to use for flat mapping.

"""

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterNumber,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterEnum,
    QgsProcessingParameterVectorLayer,
    QgsFeature,
)

from qgis import processing
from qgis.PyQt.QtCore import QVariant
from typing import Optional


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


class VtolImportDemAlgorithm(QgsProcessingAlgorithm):

    PARAMETER_OUTPUT_RASTER = "OUTPUT"
    PARAMETER_MAP_AREA = "MAP_AREA"
    PARAMETER_RESAMPLING = "PARAMETER_RESAMPLING"
    PARAMETER_TARGET_CRS = "PARAMETER_TARGET_CRS"
    PARAMETER_SOURCE_DEM = "PARAMETER_SOURCE_DEM"
    PARAMETER_SOURCE_NODATA = "PARAMETER_SOURCE_NODATA"

    OPTIONS_RESAMPLING = {
        "nearest": "nearest (blocky coasts, rough sharp peaks)",
        "bilinear": "bilinear (gently blurred coasts, softened slopes)",
        "cubic": "cubic (smoothly curved coasts, peaks with mild halos)",
        "cubicspline": "cubic spline (very smooth coasts, very smooth peaks)",
        "lanczos": "lanczos (sharp coasts, sharp peaks, some artifacts)",
        "average": "average (soft blurred coasts, flattened terrain)",
        "mode": "mode (stepped chunky coasts, plateaued peaks)",
    }
    OPTIONS_RESAMPLING_DEFAULT = list(OPTIONS_RESAMPLING.keys()).index("nearest")

    OPTION_NODATA_DEFAULT = -32768

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PARAMETER_MAP_AREA,
                self.tr("<b>Map Information</b><br><br>Map Area"),
                [QVariant.Int, 3],  # QgsWkbTypes.Polygon
            )
        )

        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.PARAMETER_SOURCE_DEM,
                self.tr("<b>Source Data</b><br><br>Input DEM Files (.hgt)"),
                QgsProcessing.TypeRaster,
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
        source_dems = self.parameterAsFileList(
            parameters, self.PARAMETER_SOURCE_DEM, context
        )
        source_nodata = self.parameterAsInt(
            parameters, self.PARAMETER_SOURCE_NODATA, context
        )
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

        feedback.setProgressText(
            self.tr(f"Importing DEM (1/2): Stitching {len(source_dems)} Sources")
        )

        # 2. Stitching
        stitched_source = processing.run(
            "gdal:buildvirtualraster",
            {
                "INPUT": source_dems,
                "RESOLUTION": 1,
                "SEPARATE": False,
                "PROJ_DIFFERENCE": False,
                "ADD_ALPHA": False,
                "ASSIGN_CRS": None,
                "RESAMPLING": 0,
                "SRC_NODATA": f"{source_nodata}",
                "EXTRA": "",
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            feedback=feedback,
            context=context,
            is_child_algorithm=True,
        )["OUTPUT"]

        feedback.setProgressText(
            self.tr(f"Importing DEM (2/2): Reprojecting Landscape")
        )

        # 3. Reprojection
        output = processing.run(
            "gdal:warpreproject",
            {
                "INPUT": stitched_source,
                "TARGET_CRS": map_area_layer.crs(),
                "RESAMPLING": target_resampling,
                "NODATA": None,
                "TARGET_RESOLUTION": None,
                "OPTIONS": "COMPRESS=DEFLATE|PREDICTOR=3|ZLEVEL=4",
                "DATA_TYPE": 6,
                "TARGET_EXTENT": None,
                "TARGET_EXTENT_CRS": None,
                "MULTITHREADING": True,
                "EXTRA": "-multi -wo NUM_THREADS=ALL_CPUS -to ALLOW_BALLPARK=NO -to ONLY_BEST=YES",
                "OUTPUT": target_file,
            },
            feedback=feedback,
            context=context,
            is_child_algorithm=True,
        )["OUTPUT"]

        context.addLayerToLoadOnCompletion(
            output,
            QgsProcessingContext.LayerDetails(
                "Digital Elevation Model", context.project()
            ),
        )

        return {self.PARAMETER_OUTPUT_RASTER: output}

    def name(self):
        return "vtolvr_dem_import"

    def displayName(self):
        return self.tr("Import DEM Data")

    def group(self):
        return self.tr("VTOL VR")

    def groupId(self):
        return "vtol_vr_maps"

    def tr(self, message):
        return message

    def createInstance(self):
        return VtolImportDemAlgorithm()

    def shortHelpString(self) -> str:
        return (
            'Imports Digital Elevation Model data from hgt files. (If you don\'t know what that means, follow "How to get hgt files?" below).<br><br>'
            "The output is returned as a new heightmap layer."
            "<h3>Parameters</h3>"
            "<h4>Map Information</h4>"
            "<ul>"
            '<li><b>Map Area:</b> Select the map area layer you have created with "Create Map Area" before.</li>'
            "</ul>"
            "<h4>Source Data</h4>"
            "<ul>"
            "<li><b>Input DEM files (.hgt):</b> Select your downloaded DEM files here (unzipped).</li>"
            "<li><b>NODATA value:</b> Default of -32768 should be fine.</li>"
            "</ul>"
            "<h4>Output</h4>"
            "<ul>"
            '<li><b>Resampling Method:</b> Smoothes the image during reprojection. I would recommend sticking to "nearest" for this operation.</li>'
            "<li><b>Output File:</b> Path to where to store the final data.</li>"
            "</ul>"
            "After the Algorithm has completed the output will appear as a new layer. "
            "<h3>How to get hgt files?</h3>"
            "hgt files contain elevation data captured via sattelites."
            "There are many free sources available online."
            "Some examples:"
            "<ul>"
            '<li><a href="https://search.earthdata.nasa.gov/search/granules?p=C2763266360-LPCLOUD&pg[0][v]=f&pg[0][gsk]=-start_date&q=C2763266360-LPCLOUD&lat=54.11987162322078&long=-165.90427657191307&zoom=12.08331745183198">NASA Earthdata</a>: Free account required. No canada, norway, russia.'
            '<li><a href="https://dwtkns.com/srtm30m/">30-Meter SRTM Tile Downloader</a>: Alternative link to NASA Earthdata (easier to use, if it works).</li>'
            '<li><a href="https://www.eorc.jaxa.jp/ALOS/en/dataset/aw3d30/aw3d30_e.htm">Advanced Land Observing Satellite</a>: Contains pretty much the whole world in 30-Meter resolution.</li>'
            "</ul>"
            "<b>NOTE:</b> The files come as ZIPs... You have to unpack them for usage."
        )
