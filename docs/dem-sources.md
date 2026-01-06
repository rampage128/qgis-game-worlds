# DEM Data Sources

qgis-game-worlds provides 3 ways to import height data for your map.
This document explains the differences as well as pros and cons of each way.

## 1. Import OpenTopography Data

[OpenTopography](https://opentopography.org/about) is a database that consolidates various state-of-the-art data sources for topographic data into one API.

**It is the recommended way to import DEM data for your map.**

Pros and Cons:
- ➕ Grants access to multiple official high-quality and state-of-the-art data sources for topographic data in one place.
- ➕ The data is raw and undeformed, which yields the best precision when creating a map.
- ➖ To use it you have to create a free account and get an API key to enter in QGIS.
- ℹ️ The number of requests is limited per day and API key. You should however not be able to reach the limit when creating maps.
- ℹ️ The area size you can download in one request is limited. This area is much larger than the allowed map areas.

## 2. Import DEM Data

This script allows to import and stitch hgt files from your local hard drive into one DEM dataset.
You would typically use it if you want to use height data from a source that can not be automatically downloaded or if you already have access to hgt files anyways.

Pros and Cons:
- ➕ Full control over the source data and quality.
- ➕ Supports most external sources, as hgt is a standardized file format.
- ➖ Requires you to manually download, store and manage the hgt files that you want to import.
- ➖ If you do not already have access, most sources will require individual accounts or API-keys.

## 3. Import XYZ Data

XYZ Data is a special kind of data used for interactive maps. It consists of small image-tiles that display a part of the world and can be used to display interactive maps like google or openstreetmap do.

At some point someone used this format to encode height data into tiles and make it available to the general puplic.
> [!WARNING]
> All providers for XYZ height data use the same set of data. So all the warnings (⚠️) listed below apply to all providers and highly depend on your area of interest that you wish to export.

Pros and Cons:
- ➕ Does not require an API key, as the original dataset is available without access control.
- ➖ Download and processing of the data is very slow. The constraints of the format produce a lot of overhead.
- ➖ No control over the data quality, resolution and quality depends on the area of interest.
- ⚠️ Deformation: The data is deformed to be displayed on a screen. While game-worlds reprojects it to be conformal again, this results in a loss of precision depending on the area of interest.
- ⚠️ Tiling/Seams: Different datasets were merged without a proper edge alignment. This can cause sharp seams in the map or individual tiles that do not match the rest of the map.
- ⚠️ Interpolation Artifacts: A lot of the high-resolution data was upscaled from lower resolution sources, which results in blobs or pillowing instead of a crisp terrain.
- ⚠️ Shoreline Noise: Underwater data (Bathymetry) was merged/blended into the data, which causes wrong elevations along coastlines and introduces terrible artifacts especially on lower zoom levels.