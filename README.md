# NEXTGEN HEXAGON – Fusion 360 Add-In

Generates honeycomb geometry **directly inside the sketch you are
currently editing**. It **never creates a new sketch** and **never runs an
extrude** – the add-in only produces sketch geometry, which you can then
use however you like.

## Installation

1. Copy the `NextGenHexagon` folder (containing `NextGenHexagon.py`,
   `NextGenHexagon.manifest` and the `resources` folder) to any location
   on disk, unchanged.
2. Fusion 360 → `Utilities` → `Add-Ins` → `Scripts and Add-Ins`.
3. `Add-Ins` tab → `+` (green plus) → select the `NextGenHexagon` folder.
4. Select the add-in → `Run` (optionally enable `Run on Startup`).
5. After starting, the **NEXTGEN HEXAGON** button appears in the `Create`
   panel of the sketch toolbar, visible as soon as a sketch is being
   edited. A confirmation dialog also appears immediately after loading,
   confirming the add-in started and telling you where to find the button.

## Usage (workflow)

1. Open Fusion 360, create or edit an existing sketch (sketch edit mode).
2. Click the **NEXTGEN HEXAGON** button.
3. In the dialog, select one or more **Boundaries**: click an already
   existing, closed profile in the current sketch (e.g. a rectangle,
   circle, or any closed polygon/arc-based profile). Click additional
   profiles to add them to the selection; ctrl/cmd-click an already
   selected one to remove it. All selected boundaries are filled with a
   single, shared hexagon grid, so adjacent areas line up seamlessly
   instead of each getting its own independently-aligned pattern.
4. Set the parameters:
   - **Hexagon Size** – *across flats* (face-to-face, inscribed circle
     diameter), just like a hex bolt head: entering `8` means an 8 mm
     wrench fits exactly. The circumradius is calculated automatically
     internally (`radius = Hexagon Size / sqrt(3)`).
   - **Gap** – extra spacing between neighboring cells
   - **Orientation** – `Flat-Top` or `Pointy-Top`
5. Press `OK` / `Generate`.

The add-in then adds only new `SketchLine` objects to the **already open**
sketch:

- Hexagons that lie fully inside the boundary are created as complete,
  closed 6-edge line loops → Fusion automatically recognizes them as
  independent closed profiles.
- Hexagons that cross the boundary are trimmed at the boundary: only the
  portions that lie *inside* are created as new lines. Since the boundary
  curve itself already exists in the sketch, Fusion automatically
  recognizes the resulting closed partial profiles ("boundary hexagons").

After generating:

- The sketch stays active and editable (move/delete lines, keep
  sketching, add dimensions, etc.).
- You can select any hexagon profile or boundary profile in the sketch
  for an extrude yourself – the add-in itself never extrudes anything.

## Behavior outside sketch edit mode

If the command is started while no sketch is currently being edited
(e.g. in the model workspace, or with no sketch open), the following
message appears:

> "Please open and edit a sketch first."

In this case, **no geometry is created** and **no new sketch is added**.

## Cleaner preview (points hidden automatically)

While the NEXTGEN HEXAGON dialog is open, sketch point markers are
temporarily hidden so the hexagon pattern (and its live preview) is
easier to read - especially with small hexagons or a tight Gap, where
Fusion's point markers can otherwise visually dominate the thin lines.

This only affects the sketch's "Show Points" setting for as long as the
dialog is open. As soon as the dialog closes - whether by clicking OK,
Cancel, or the X - the original setting is restored automatically. Your
sketch's normal point display is never permanently changed.

## Live preview

As you adjust Boundary, Hexagon Size, Gap, or Orientation in the dialog,
the honeycomb pattern updates live in the viewport before you click OK.
This uses Fusion's standard preview mechanism (the same one built-in
commands like Extrude use): Fusion automatically discards the preview
geometry each time an input changes, and only makes it permanent once you
click OK. Nothing is committed to the sketch until then, so you can
freely try different sizes/gaps without creating any leftover geometry
to undo.

Note: for boundaries that generate a very large number of cells, the live
preview may feel a little less responsive while dragging a value, since
the full pattern is recomputed on every change.

## Getting help inside Fusion

While the NEXTGEN HEXAGON command dialog is open, a help button appears
in the lower-left corner of the dialog, and pressing **F1** shows the
same page: a local HTML quick-reference (`resources/help.html`) covering
the workflow and parameters.

## Technical notes / deliberate simplifications

- Boundary detection uses Fusion's computed `Profile` object (selection
  filter `Profiles`), so contours made up of several curves, or curved
  contours, are supported as well (arcs/splines are tessellated into
  polylines with a tight tolerance internally).
- Clipping the boundary hexagons uses a general segment-vs-polygon
  clipping algorithm that also works with slightly concave contours. Arcs
  and splines are first tessellated finely into a polyline for the
  calculation; every computed intersection point is then projected back
  exactly onto the real curve geometry, so the new sketch lines really end
  on the existing boundary curve (no tiny gap, no open profile) – even for
  complex, curved contours.
- For performance, `sketch.isComputeDeferred` is enabled during geometry
  generation and reset afterwards.

## Planned extension (not part of this version)

A second command could later be added that creates a new sketch on
demand. This first version focuses exclusively on the sketch that is
already being actively edited.
