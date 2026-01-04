"""
OpenTopo IMPORTER:

This essentially does two things:

1. Import DEM data from opentopography based on a selected map area
2. Warp to target CRS

It simplifies the process of importing height data to be ready to use for flat mapping.
It will only import the minimum required area for the given map area.

"""

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterRasterDestination,
    QgsProcessingFeedback,
    QgsProject,
    QgsProcessingParameterEnum,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProcessingUtils,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsFeature,
    QgsSettings,
    Qgis,
)

from qgis.PyQt.QtCore import QVariant
from qgis import processing
from typing import Optional
import requests


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


class VtolImportOpenTopoAlgorithm(QgsProcessingAlgorithm):

    PARAMETER_OUTPUT_RASTER = "OUTPUT"
    PARAMETER_RESAMPLING = "RESAMPLING"
    PARAMETER_MAP_AREA = "MAP_AREA"
    PARAMETER_DEM_SOURCE = "DEM_SOURCE"
    PARAMETER_API_KEY = "API_KEY"

    OPTIONS_DEM = {
        "COP30": "Copernicus 30m (global, super crisp, super accurate)",
        "EU_DTM": "EU Bare Earth DTM (EU only, pretty blurry)",
        "AW3D30": "ALOS World 3D (global, super crisp, more noise/artifacts)",
        "NASADEM": "NASADEM 30m (global, a bit blurry but solid)",
        # "SRTM15Plus": 'Bathymetry (What a weird word for "Ocean Floor")',
    }
    OPTIONS_DEM_DEFAULT = list(OPTIONS_DEM.keys()).index("COP30")

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

    SETTING_API_KEY_FALLBACK = "OpenTopographyDEMDownloader/ot_api_key"
    SETTING_API_KEY = "gameworlds/opentopo/api_key"

    def initAlgorithm(self, config=None):
        settings = QgsSettings()
        last_known_api_key = settings.value(
            self.SETTING_API_KEY, settings.value(self.SETTING_API_KEY_FALLBACK, "")
        )

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PARAMETER_MAP_AREA,
                self.tr("<b>Map Information</b><br><br>Map Area"),
                [QVariant.Int, 3],  # QgsWkbTypes.Polygon
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAMETER_DEM_SOURCE,
                self.tr("<hr><br><b>Source Data</b><br><br>DEM Source"),
                list(self.OPTIONS_DEM.values()),
                defaultValue=self.OPTIONS_DEM_DEFAULT,
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.PARAMETER_API_KEY,
                self.tr("OpenTopography API Key"),
                defaultValue=last_known_api_key,
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
        dem_source_index = self.parameterAsEnum(
            parameters, self.PARAMETER_DEM_SOURCE, context
        )
        dem_source = list(self.OPTIONS_DEM.keys())[dem_source_index]
        api_key = self.parameterAsString(parameters, self.PARAMETER_API_KEY, context)
        QgsSettings().setValue(self.SETTING_API_KEY, api_key)
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

        feedback.setProgressText(self.tr(f"Importing DEM (1/2): Fetching DEM Data"))

        project_context = QgsProject.instance().transformContext()
        epsg_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
        if not project_context.hasTransform(map_area_layer.crs(), epsg_4326):
            operation = project_context.calculateCoordinateOperation(
                map_area_layer.crs(), epsg_4326
            )
            project_context.addCoordinateOperation(
                map_area_layer.crs(), epsg_4326, operation
            )
            QgsProject.instance().setTransformContext(project_context)
        to_pseudo = QgsCoordinateTransform(
            map_area_layer.crs(), epsg_4326, project_context
        )

        if not to_pseudo.isValid():
            raise QgsProcessingException(
                f"Transform from {map_area_layer.crs().authid()} to {epsg_4326.authid()} is not valid."
            )

        map_area_geometry = QgsGeometry.fromRect(map_area_layer.extent())
        transform_result = map_area_geometry.transform(to_pseudo)

        if transform_result != Qgis.GeometryOperationResult.Success:
            raise QgsProcessingException(
                f"Transform from {map_area_layer.crs().authid()} to {epsg_4326.authid()} failed with status: {transform_result}"
            )

        xyz_extent = map_area_geometry.boundingBox()

        dem_data = self._download_dem(dem_source, xyz_extent, api_key, feedback)

        if feedback.isCanceled():
            return {}

        feedback.setProgressText(
            self.tr(f"Importing DEM (2/2): Reprojecting Landscape")
        )

        # 3. Reprojection
        output = processing.run(
            "gdal:warpreproject",
            {
                "INPUT": dem_data,
                # "SOURCE_CRS": epsg_4326,
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
                f"{area["name"] (opentopo)}", context.project()
            ),
        )

        return {self.PARAMETER_OUTPUT_RASTER: output}

    def _download_dem(
        self,
        dem_source: str,
        extent: QgsRectangle,
        api_key: str,
        feedback: QgsProcessingFeedback,
    ):
        url = (
            f"https://portal.opentopography.org/API/globaldem?"
            f"demtype={dem_source}&"
            f"south={extent.yMinimum()}&north={extent.yMaximum()}&"
            f"west={extent.xMinimum()}&east={extent.xMaximum()}&"
            f"outputFormat=GTiff&API_Key={api_key}"
        )

        feedback.pushInfo(f"Downloading from: {url}")

        try:
            with requests.get(url, stream=True, timeout=20) as r:
                r.raise_for_status()

                # Determine file size for the progress bar
                total_size = int(r.headers.get("content-length", 0))
                downloaded = 0

                temp_tif = QgsProcessingUtils.generateTempFilename("opentopo.tif")

                with open(temp_tif, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1MB Chunks
                        if feedback.isCanceled():
                            return {}

                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            percentage = (downloaded / total_size) * 100
                            feedback.setProgress(percentage)
                            # feedback.setProgressText(f"Downloading: {percentage:.1f}%")

                return temp_tif
        except requests.exceptions.HTTPError as e:
            raise QgsProcessingException(
                f"OpenTopo Server Error: {e.response.text[:200]}"
            )
        except Exception as e:
            raise QgsProcessingException(f"K11 Telemetry Failure: {str(e)}")

        return None

    def name(self):
        return "vtolvr_opentopo_import"

    def displayName(self):
        return self.tr("Import OpenTopography Data")

    def group(self):
        return self.tr("VTOL VR")

    def groupId(self):
        return "vtol_vr_maps"

    def tr(self, message):
        return message

    def createInstance(self):
        return VtolImportOpenTopoAlgorithm()

    def shortHelpString(self) -> str:
        return (
            "Imports height data from OpenTopography. "
            "That means that you will not need to download any DEM files manually."
            "The output is returned as a new heightmap layer."
            '<div><b>NOTE: You will need a (free) account and API key at: <a href="https://opentopography.org/">opentopography.org</a>.</b></div>'
            "<h3>Parameters</h3>"
            "<h4>Map Information</h4>"
            "<ul>"
            '<li><b>Map Area:</b> Select the map area layer you have created with "Create Map Area" before.</li>'
            "</ul>"
            "<h4>Source Data</h4>"
            "<ul>"
            "<li><b>DEM Source:</b> Select your preferred DEM source.</li>"
            '<li><b>OpenTopography API Key:</b> Your API Key from <a href="https://opentopography.org/">opentopography.org</a>.</li>'
            "</ul>"
            "<h4>Output</h4>"
            "<ul>"
            '<li><b>Resampling Method:</b> Smoothes the image during reprojection. I would recommend sticking to "nearest" for this operation.</li>'
            "<li><b>Output File:</b> Path to where to store the final data.</li>"
            "</ul>"
            "After the Algorithm has completed the output will appear as a new layer. "
        )
