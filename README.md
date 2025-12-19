# QGIS Game Worlds
> Create VOTL VR custom maps using real world terrain in QGIS

If you want to recreate your favorite real-world area as mission theater in VTOL VR, you are in the right place. You can use these scripts to directly export detailed terrain for use in the game.

## Getting Started

### Required Applications
- [QGIS](https://qgis.org/) (A geographic analysis application)
- [VTOL VR](https://vtolvr.bdynamicsstudio.com/) (duh!)

> [!IMPORTANT]
> Use QGIS version `3.40.12-Bratislava` or `3.40.x` for maximum compatibility.

### Installing the scripts

1. Start QGIS.
2. Select `View` -> `Panels` -> `Processing Toolbox` from the top menu.
   - *(A new panel with the title `Processing Toolbox` should appear.)*
3. Click the icon that looks like a :wrench: (titled `Options`)
   - *(A dialog titled `Options --- Processing` should open)*
4. Expand The item `Scripts` in the list of settings (on the right)
5. Open the path shown next to `Scripts folder(s)` in your file-explorer of choice.
6. Download all python (`.py`) files from this repo and place them in the folder.

## Making a Map

We will mainly use the `Processing Toolbox` to execute the scripts. They aim to reduce as much of the effort as possible and try to group operations together.

You will need a bit of understanding of QGIS and it can seem overwhelming at first... But don't worry, we will only use <1% of all features.

Start a new QGIS project and open the `Processing Toolbox` under `View` -> `Panels`, if it is not open yet on the right.

In the toolbox expand `Scripts` (at the very bottom) and expand `VTOL VR` that should be in there.
You will see the scripts. 

Important to note:
- Double clicking a script will open the UI for that operation.
- The UI has two steps:
  - Filling in the parameters (has step by step instructions on the right)
  - Running the script
- When you run a script, the dialog will stay open when finished. You have to close it manually.
- Any output that was generated will be added as a layer in your main QGIS window.

Just run the scripts in this order:

1. Create Map Area: Allows you to create a map area that is used to export part of your height data. Map areas can be moved around and contain the config of a map.
2. Importing height data (you only need one of those)
   - Import OpenTopography Data: Directly downloads high quality DEM data from OpenTopography.
   - Import DEM Data: Allows you to select manually downloaded .hgt files to start a high quality heightmap.
   - Import XYZ Data: Allows you to import height data without any manual download (easier but slow and results may vary).
3. [Optional] Create City Zones: Provides you with a way to draw (and edit) the VTOL VR cities. If you do not care about cities you can skip this step.
4. Export Map Area: This step takes the data from the other 3 steps and allows you to do a final export of all the required game files.

> [!TIP]
> Each script has embedded documentation that should appear on the right side. Also the scripts take care of all the complications of geographic projection for you, so you do not have to worry about any of that.

> [!NOTE]
> If you want to draw cities, that will require some QGIS interaction to draw polygons, the `Create City Zones` script has some steps in the embedded documentation.

**That's it!** 

If you want to know more about why this seems so complicated, you may read on.

## Additional Info

QGIS is a software used for geographical analysis. It has a huge array of features. That means that map creation requires getting used to QGIS. But don't worry, there are only a few things we absolutely need and some other stuff that can be useful to know.

The most important parts are
- Geographical Projections
- Projects and files
- Understanding the User-Interface

### Geographical Projections

VTOL VR uses a flat plane as basis for it's maps. That means there is no earth curvature. And even worse, in reality the earth curvature is not the same everywhere. 

The world is measured in degrees of rotation from 0 to 360, not in km or miles. On top of that a degree is not the same amount of km depending on where on earth we measure.

A degree of longitude (east to west) has less km inside of it, the further away we are from the Equator.
This is compensated by projection. Earth is not a perfect sphere, so additional local inacurracies can pop up. Last but not least this also means map data further from the Prime Meridian (0 degrees) can be skewed or rotated.

The best results are achieved when we use special projections for our area of interest. QGIS helps us do that.

### Projects and Files

QGIS has two ways to do things: 
- One is using temporary files that are automatically deleted when the current project or the application is closed.
- The second is saving your project and saving the data that we need as dedicated files.

I would recommend to create a project for each area you want to work with. It is possible to load a larger area of the world into QGIS and create multiple VTOL VR maps inside of one project. But this has some limits.

### Understanding the User-Interface

Apart from the option to work with temporary files, which you can decide at each step in the application, most actions in QGIS work like this:

You pick an operation on some input data that has some parameters to set and you define if your output is a file or temporary.

My scripts aim to reduce as much of that effort as possible and try to group operations together.

### About Elevation Data

QGIS allows us to use real terrain elevation data, also known as <abbr title="Digital Elevation Model">DEM</abbr> or <abbr title="Shuttle Radar Topography Mission">SRTM</abbr>. This data is usually stored in `hgt`-files. Each file is a single square tile that spans 1° by 1° of the world surface. 

Datasets come in two flavors:

| Flavor | Pixel Size           | Total Size | File Size |
|--------|----------------------|------------|----------:|
| SRTM1  | ~30m (1 arc-second)  | 3601x3601  |     ~25MB |
| SRTM3  | ~90m (3 arc-seconds) | 1201x1201  |      ~3MB |

> [!Note] 
> There are other formats available which usually store high resolution data. But these are not of interest for our purpose. QGIS should however be able to handle all kinds of <abbr title="Digital Elevation Model">DEM</abbr>-Formats.

VTOL VR has a horizontal terrain resolution of 153.6m per terrain segment. So `SRTM3` is already decent. 30m can be beneficial as these scripts use a "smart downsampling", so details might be preserved better in the final output with `SRTM1`.

Here are some sources to download height data form:

- [30-Meter SRTM Tile Downloader](https://dwtkns.com/srtm30m/): Does not cover the polar caps, canada, norway, russia or anything else on that height. (90-meter is available too).
- [Advanced Land Observing Satellite](https://www.eorc.jaxa.jp/ALOS/en/dataset/aw3d30/aw3d30_e.htm): Contains pretty much the whole world.

> [!NOTE]
> Both sources require you to register a *free* account for downloading. There are tons of other more localized sources, but most of them require a registration.
