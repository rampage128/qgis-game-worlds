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

### Preparation

Before we can create a map we need a bit of (one time) preparation:

- Start a new QGIS project and open the `Processing Toolbox` under `View` -> `Panels`, if it is not open yet on the right.
- In the toolbox expand `Scripts` (at the very bottom) and expand `VTOL VR` that should be in there.
You will see the scripts. 

Important to note:
- Double clicking a script will open the UI for that operation.
- The UI has two steps:
  - Filling in the parameters (has step by step instructions on the right)
  - Running the script
- When you run a script, the dialog will print out information about the process.
- Any output that was generated will be added as a layer in your main QGIS window.
- When the script is finished you can check the results, and close the dialog.

### Step 1: Map Area

Run the script `Create Map Area`. It allows you to select the location, size and map options. All the following steps rely on a map area to be present. After finishing, you should see a box in the main window that shows your map area.

### Step 2: Import Height Data

To get some landscaping going, we have 3 alternatives. 

You only need to run **one** of the following scripts:
- `Import OpenTopography Data` (recommended): Directly downloads high quality DEM data from OpenTopography.org.
- `Import DEM Data`: Allows you to select manually downloaded .hgt files to start a high quality heightmap.
- `Import XYZ Data`: Allows you to import height data without any manual download (easier but slow and results may vary).

> [!TIP]
> Read more about the different sources [here](./docs/dem-sources.md). (I recommend using OpenTopography.)

### Step 3: Cities

This step is optional. It allows you to draw and edit cities in QGIS directly, which is more accurate and convenient than painting them ingame.

Run `Create City Zones`. It will create a special empty layer for you to draw cities on.  

> [!TIP]
> Even if you do not want to save a project, I recommend saving the output to a file if you want to edit the cities later.

> [!NOTE]
> If you want to draw cities, you need to draw polygons on the city layer. Here is some documentation on how that works: [QGIS layer digitizing](https://docs.qgis.org/3.40/en/docs/user_manual/working_with_vector/editing_geometry_attributes.html#digitizing-an-existing-layer)

### Step 4: Export
Run `Export Map Area`. This step takes the data from the other steps and allows you to do a final export of all the required game files. Select a folder as output destination. This folder will be your ready-to-use map.

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
