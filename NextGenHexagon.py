"""
NEXTGEN HEXAGON - Fusion 360 Add-In
=====================================

CORE DESIGN PRINCIPLE:
- The add-in is meant to be launched WHILE the user is already editing an
  existing sketch (sketch edit mode).
- It NEVER creates a new sketch.
- All generated geometry (hexagon lines, boundary-clipped hexagons,
  intersection points) is added as real SketchCurve geometry inside the
  already-open sketch.
- It NEVER creates an extrude or a solid body automatically.
- If no sketch is currently being edited when the command is started, the
  add-in shows the message "Please open and edit a sketch first." and
  aborts without creating any geometry at all.

Folder structure (Fusion add-in convention):
    NextGenHexagon/
        NextGenHexagon.py          <- this file
        NextGenHexagon.manifest
        resources/
            16x16.png
            32x32.png
            64x64.png
"""

import math
import os
import traceback

import adsk.core
import adsk.fusion

# ---------------------------------------------------------------------------
# Global references (must be kept alive, otherwise Python garbage-collects
# the event handlers and the callbacks stop firing - standard pattern for
# Fusion 360 add-ins).
# ---------------------------------------------------------------------------
_app = None
_ui = None
_handlers = []

_CMD_ID = 'nextGenHexagonCmd'
_CMD_NAME = 'NEXTGEN HEXAGON'
_CMD_TOOLTIP = 'Generates honeycomb geometry inside the currently edited sketch'
_PANEL_ID = 'SketchCreatePanel'  # panel that is visible in sketch edit mode
_RESOURCE_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')
_HELP_FILE_PATH = os.path.join(_RESOURCE_FOLDER, 'help.html')

_ORIENTATIONS = ['Flat-Top', 'Pointy-Top']


# ---------------------------------------------------------------------------
# Geometry helper functions (pure Python math, 2D, in sketch coordinates)
# ---------------------------------------------------------------------------

def _polygon_signed_area(points):
    """Signed area of a polygon (shoelace formula). > 0 = CCW."""
    area = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area * 0.5


def _point_in_polygon(pt, polygon):
    """Ray-casting point-in-polygon test (also works for concave polygons,
    as long as they are not self-intersecting)."""
    x, y = pt
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and \
            (x < (xj - xi) * (y - yi) / ((yj - yi) if (yj - yi) != 0 else 1e-12) + xi)
        if intersects:
            inside = not inside
        j = i
    return inside


def _seg_intersect(p1, p2, p3, p4):
    """Intersection point of two line segments p1-p2 and p3-p4.
    Returns: (t, point) with t = parameter along p1-p2 (0..1),
    or None if there is no intersection within both segments."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-12:
        return None  # parallel

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / denom

    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        ix = x1 + t * (x2 - x1)
        iy = y1 + t * (y2 - y1)
        return t, (ix, iy)
    return None


def _project_point_to_curve(point_xy, curve3d):
    """Projects an (approximate) point exactly back onto the real curve
    geometry (arc/spline/line), so that new sketch lines really end on the
    existing boundary curve (no tiny gap). Returns the unchanged
    approximate point if the projection fails."""
    if curve3d is None:
        return point_xy
    try:
        p3d = adsk.core.Point3D.create(point_xy[0], point_xy[1], 0)
        evaluator = curve3d.evaluator
        ok, param = evaluator.getParameterAtPoint(p3d)
        if not ok:
            return point_xy
        ok2, exact_point = evaluator.getPointAtParameter(param)
        if not ok2:
            return point_xy
        return (exact_point.x, exact_point.y)
    except Exception:
        return point_xy


def _clip_segment_to_polygon(p1, p2, polygon, edge_sources=None):
    """Clips a line segment against a (possibly concave) polygon and
    returns the sub-segments that lie inside the polygon.
    If 'edge_sources' is provided (the real curve for each polygon edge),
    the intersection points are projected exactly back onto the real curve
    so that no gap remains to the actual boundary geometry.
    Returns: list of (a, b) point pairs."""
    breakpoints = [(0.0, p1), (1.0, p2)]
    n = len(polygon)
    for i in range(n):
        a = polygon[i]
        b = polygon[(i + 1) % n]
        result = _seg_intersect(p1, p2, a, b)
        if result is not None:
            t, pt = result
            if 1e-9 < t < 1 - 1e-9:
                if edge_sources is not None and i < len(edge_sources):
                    pt = _project_point_to_curve(pt, edge_sources[i])
                breakpoints.append((t, pt))

    breakpoints.sort(key=lambda item: item[0])
    dedup = []
    for t, pt in breakpoints:
        if dedup and abs(t - dedup[-1][0]) < 1e-9:
            continue
        dedup.append((t, pt))

    kept = []
    for i in range(len(dedup) - 1):
        t0, a = dedup[i]
        t1, b = dedup[i + 1]
        if t1 - t0 < 1e-9:
            continue
        tm = (t0 + t1) / 2.0
        mid = (p1[0] + tm * (p2[0] - p1[0]), p1[1] + tm * (p2[1] - p1[1]))
        if _point_in_polygon(mid, polygon):
            kept.append((a, b))
    return kept


def _hexagon_vertices(cx, cy, radius, orientation):
    """Returns the 6 vertices of a regular hexagon.
    'radius' is the circumradius (center -> vertex / circumscribed)."""
    verts = []
    start_angle = 0.0 if orientation == 'Flat-Top' else 30.0
    for k in range(6):
        angle = math.radians(start_angle + 60.0 * k)
        verts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return verts


def _tessellate_curve3d(curve3d):
    """Converts a Curve3D (Line3D, Arc3D, NurbsCurve3D, ...) into a list of
    2D points (x, y) in sketch coordinates."""
    points = []
    try:
        evaluator = curve3d.evaluator
        ok, start_p, end_p = evaluator.getParameterExtents()
        if not ok:
            raise RuntimeError('Could not determine parameter extents')
        ok, strokes = evaluator.getStrokes(start_p, end_p, 0.002)
        if not ok or not strokes:
            raise RuntimeError('Tessellation failed')
        for p in strokes:
            points.append((p.x, p.y))
    except Exception:
        # Fallback: use only start/end point
        try:
            sp = curve3d.startPoint
            ep = curve3d.endPoint
            points = [(sp.x, sp.y), (ep.x, ep.y)]
        except Exception:
            pass
    return points


def _get_boundary_polygon_from_profile(profile):
    """Extracts the outer contour of a Fusion profile as a list of (x, y)
    points in sketch coordinates, together in parallel with the real curve
    geometry (arc/spline/line) for each polygon edge, so that intersection
    points can later be projected exactly back onto the real boundary curve
    (no gap for complex contours).
    Returns: (polygon, edge_sources) with len(edge_sources) == len(polygon).
    edge_sources[i] is the source curve of edge polygon[i] -> polygon[i+1]."""
    # Tolerance used to decide whether two tessellated points represent the
    # same shared vertex between curves. Needs to be generous enough to
    # absorb floating-point noise from curve tessellation (arcs/splines),
    # not just bit-exact coordinates.
    join_tol = 1e-4

    outer_loop = None
    for loop in profile.profileLoops:
        if loop.isOuter:
            outer_loop = loop
            break
    if outer_loop is None and profile.profileLoops.count > 0:
        outer_loop = profile.profileLoops.item(0)
    if outer_loop is None:
        return [], []

    polygon = []
    edge_sources = []
    for profile_curve in outer_loop.profileCurves:
        geom = profile_curve.geometry
        pts = _tessellate_curve3d(geom)
        if not pts:
            continue
        if polygon and _dist(polygon[-1], pts[0]) < join_tol:
            pts = pts[1:]
        for p in pts:
            if polygon:
                edge_sources.append(geom)
            polygon.append(p)

    # Remove the duplicate at the end if the contour was returned closed.
    # The corresponding entry in edge_sources is kept, since it represents
    # the closing edge (last point -> first point).
    if len(polygon) > 1 and _dist(polygon[0], polygon[-1]) < join_tol:
        polygon.pop()

    # Defensive safety net: polygon and edge_sources must always be the
    # same length (edge_sources[i] describes edge polygon[i] -> polygon[i+1]).
    # If floating-point edge cases ever cause a mismatch, truncate to the
    # shorter one rather than risk an out-of-range crash later. Curve
    # snapping is simply skipped for any edge left without a known source.
    if len(edge_sources) != len(polygon):
        min_len = min(len(edge_sources), len(polygon))
        polygon = polygon[:min_len] if min_len < len(polygon) else polygon
        edge_sources = edge_sources[:len(polygon)]
        while len(edge_sources) < len(polygon):
            edge_sources.append(None)

    return polygon, edge_sources


def _dist(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def _bounding_box(polygon):
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _hex_grid_centers(min_x, min_y, max_x, max_y, radius, gap, orientation):
    """Generates the centers of a honeycomb grid that covers the bounding
    box of the boundary (including a margin of one cell size)."""
    centers = []
    sqrt3 = math.sqrt(3.0)
    pad = 2.0 * radius + gap * 2.0

    if orientation == 'Pointy-Top':
        dx = sqrt3 * radius + gap
        dy = 1.5 * radius + gap * (sqrt3 / 2.0)
        row = 0
        y = min_y - pad
        while y <= max_y + pad:
            x_offset = (dx / 2.0) if (row % 2 == 1) else 0.0
            x = min_x - pad + x_offset
            while x <= max_x + pad:
                centers.append((x, y))
                x += dx
            y += dy
            row += 1
    else:  # Flat-Top
        dx = 1.5 * radius + gap * (sqrt3 / 2.0)
        dy = sqrt3 * radius + gap
        col = 0
        x = min_x - pad
        while x <= max_x + pad:
            y_offset = (dy / 2.0) if (col % 2 == 1) else 0.0
            y = min_y - pad + y_offset
            while y <= max_y + pad:
                centers.append((x, y))
                y += dy
            x += dx
            col += 1

    return centers


def _all_inside(points, polygon):
    return all(_point_in_polygon(p, polygon) for p in points)


def _any_inside_or_crossing(verts, polygon):
    if any(_point_in_polygon(v, polygon) for v in verts):
        return True
    n = len(verts)
    for i in range(n):
        a = verts[i]
        b = verts[(i + 1) % n]
        if _clip_segment_to_polygon(a, b, polygon):
            return True
    return False


def _edge_key(p1, p2, precision=6):
    """Canonical, order-independent key for a line segment, used to detect
    when two neighboring hexagons would draw the exact same shared wall
    twice (which would double the line count and make Fusion show large
    coincident-point markers at every shared vertex)."""
    a = (round(p1[0], precision), round(p1[1], precision))
    b = (round(p2[0], precision), round(p2[1], precision))
    return (a, b) if a <= b else (b, a)


def _generate_honeycomb(sketch, boundary_polygon, edge_sources, radius, gap, orientation):
    """Generates the honeycomb geometry as SketchLines inside the given
    sketch. Returns (full_hex_count, partial_hex_count)."""

    min_x, min_y, max_x, max_y = _bounding_box(boundary_polygon)
    centers = _hex_grid_centers(min_x, min_y, max_x, max_y, radius, gap, orientation)

    lines = sketch.sketchCurves.sketchLines
    full_count = 0
    partial_count = 0

    # Neighboring hexagons share a wall. Without deduplication, both
    # hexagons would each draw that same wall, doubling the line count and
    # causing Fusion to display large coincident-point markers at every
    # shared vertex. This set makes sure every physical wall is only
    # created once, regardless of how many hexagons touch it.
    drawn_edges = set()

    def _add_line_if_new(a, b):
        key = _edge_key(a, b)
        if key in drawn_edges:
            return False
        drawn_edges.add(key)
        lines.addByTwoPoints(
            adsk.core.Point3D.create(a[0], a[1], 0),
            adsk.core.Point3D.create(b[0], b[1], 0))
        return True

    was_deferred = sketch.isComputeDeferred
    sketch.isComputeDeferred = True
    try:
        for (cx, cy) in centers:
            verts = _hexagon_vertices(cx, cy, radius, orientation)

            if _all_inside(verts, boundary_polygon):
                # Complete hexagon lies fully inside the boundary
                for i in range(6):
                    p1 = verts[i]
                    p2 = verts[(i + 1) % 6]
                    _add_line_if_new(p1, p2)
                full_count += 1
            else:
                if not _any_inside_or_crossing(verts, boundary_polygon):
                    continue
                any_segment_added = False
                for i in range(6):
                    p1 = verts[i]
                    p2 = verts[(i + 1) % 6]
                    kept_segments = _clip_segment_to_polygon(
                        p1, p2, boundary_polygon, edge_sources)
                    for (a, b) in kept_segments:
                        if _dist(a, b) < 1e-6:
                            continue
                        _add_line_if_new(a, b)
                        any_segment_added = True
                if any_segment_added:
                    partial_count += 1
    finally:
        sketch.isComputeDeferred = was_deferred

    return full_count, partial_count


# ---------------------------------------------------------------------------
# Fusion 360 event handlers
# ---------------------------------------------------------------------------

class HoneycombCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            cmd = args.command
            inputs = cmd.commandInputs

            design = adsk.fusion.Design.cast(_app.activeProduct)
            if not design:
                _ui.messageBox('No active Fusion 360 design is open.')
                args.isCancelled = True
                return

            active_sketch = adsk.fusion.Sketch.cast(design.activeEditObject)
            if active_sketch is None:
                _ui.messageBox('Please open and edit a sketch first.')
                args.isCancelled = True
                return

            # Temporarily hide sketch point markers while this command is
            # active, so the honeycomb pattern (and its live preview) is
            # easier to read. The original setting is restored automatically
            # in HoneycombDestroyHandler once the command ends (OK, Cancel,
            # or closing the dialog) - this never changes the user's normal
            # sketch display setting permanently.
            points_were_shown = active_sketch.arePointsShown
            try:
                active_sketch.arePointsShown = False
            except Exception:
                pass

            selection_input = inputs.addSelectionInput(
                'boundarySelection', 'Boundary',
                'Select a closed profile / contour in the current sketch')
            selection_input.addSelectionFilter('Profiles')
            selection_input.setSelectionLimits(1, 1)

            size_input = inputs.addValueInput(
                'hexSize', 'Hexagon Size', 'mm',
                adsk.core.ValueInput.createByReal(1.0))
            size_input.tooltip = ('Across flats (face-to-face): distance '
                                   'between two opposite edges, e.g. "8" = '
                                   'an 8 mm wrench fits exactly.')

            gap_input = inputs.addValueInput(
                'gap', 'Gap', 'mm',
                adsk.core.ValueInput.createByReal(0.2))

            orientation_input = inputs.addDropDownCommandInput(
                'orientation', 'Orientation',
                adsk.core.DropDownStyles.TextListDropDownStyle)
            for o in _ORIENTATIONS:
                orientation_input.listItems.add(o, o == _ORIENTATIONS[0])

            # Register execute / validate / preview handlers
            on_execute = HoneycombCommandExecuteHandler(active_sketch)
            cmd.execute.add(on_execute)
            _handlers.append(on_execute)

            on_preview = HoneycombPreviewHandler(active_sketch)
            cmd.executePreview.add(on_preview)
            _handlers.append(on_preview)

            on_validate = HoneycombValidateHandler()
            cmd.validateInputs.add(on_validate)
            _handlers.append(on_validate)

            on_destroy = HoneycombDestroyHandler(active_sketch, points_were_shown)
            cmd.destroy.add(on_destroy)
            _handlers.append(on_destroy)

            # F1 / Help support: Fusion shows a help button in the dialog
            # (and responds to F1) automatically whenever helpFile is set
            # to a local HTML file. No event handler is needed for this.
            if os.path.isfile(_HELP_FILE_PATH):
                cmd.helpFile = _HELP_FILE_PATH

        except Exception:
            if _ui:
                _ui.messageBox('Error creating the command:\n{}'.format(
                    traceback.format_exc()))


class HoneycombValidateHandler(adsk.core.ValidateInputsEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            inputs = args.inputs
            size_val = inputs.itemById('hexSize').value
            gap_val = inputs.itemById('gap').value
            boundary_sel = inputs.itemById('boundarySelection')

            valid = True
            if size_val <= 0:
                valid = False
            if gap_val < 0:
                valid = False
            if boundary_sel.selectionCount < 1:
                valid = False

            args.areInputsValid = valid
        except Exception:
            args.areInputsValid = False


def _try_collect_generation_params(cmd_inputs, sketch):
    """Reads and validates the current command inputs. Returns a dict with
    the resolved boundary/curve data and hexagon parameters on success, or
    None if the current input state is not yet complete/valid (e.g. no
    boundary selected yet, or size is zero). This never shows a message
    box - it's used both by the live preview (where an incomplete state is
    completely normal while the user is still adjusting things) and by the
    final execute handler (which decides itself whether/what to tell the
    user)."""
    boundary_input = cmd_inputs.itemById('boundarySelection')
    if boundary_input.selectionCount < 1:
        return None
    selected_entity = boundary_input.selection(0).entity
    profile = adsk.fusion.Profile.cast(selected_entity)
    if profile is None:
        return None
    if profile.parentSketch.entityToken != sketch.entityToken:
        return None

    hex_size_cm = cmd_inputs.itemById('hexSize').value
    gap_cm = cmd_inputs.itemById('gap').value
    if hex_size_cm <= 0 or gap_cm < 0:
        return None

    orientation_dropdown = cmd_inputs.itemById('orientation')
    orientation = orientation_dropdown.selectedItem.name

    # Hexagon Size is interpreted as "across flats" (face-to-face,
    # inscribed circle diameter), just like a hex bolt head (e.g.
    # "8" = an 8 mm wrench fits exactly).
    # Conversion across-flats (AF) -> circumradius (center->vertex):
    #   AF = radius * sqrt(3)  =>  radius = AF / sqrt(3)
    hex_radius_cm = hex_size_cm / math.sqrt(3.0)

    boundary_polygon, edge_sources = _get_boundary_polygon_from_profile(profile)
    if len(boundary_polygon) < 3:
        return None

    return {
        'profile': profile,
        'boundary_polygon': boundary_polygon,
        'edge_sources': edge_sources,
        'hex_radius_cm': hex_radius_cm,
        'gap_cm': gap_cm,
        'orientation': orientation,
    }


class HoneycombCommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, active_sketch):
        super().__init__()
        self._active_sketch = active_sketch

    def notify(self, args):
        try:
            cmd_inputs = args.command.commandInputs

            design = adsk.fusion.Design.cast(_app.activeProduct)
            current_edit_sketch = adsk.fusion.Sketch.cast(design.activeEditObject) \
                if design else None

            # Safety check: the sketch being edited must still be the exact
            # sketch the command was started in.
            if current_edit_sketch is None or \
                    current_edit_sketch.entityToken != self._active_sketch.entityToken:
                _ui.messageBox('Please open and edit a sketch first.')
                return

            sketch = self._active_sketch

            params = _try_collect_generation_params(cmd_inputs, sketch)
            if params is None:
                _ui.messageBox('Please select a closed boundary profile and '
                                'make sure Hexagon Size / Gap are valid '
                                'before generating.')
                return

            full_count, partial_count = _generate_honeycomb(
                sketch, params['boundary_polygon'], params['edge_sources'],
                radius=params['hex_radius_cm'], gap=params['gap_cm'],
                orientation=params['orientation'])

            if full_count == 0 and partial_count == 0:
                _ui.messageBox('No hexagons could be generated inside the '
                                'selected boundary. Please check Hexagon '
                                'Size / Gap.')
            else:
                _app.log('NEXTGEN HEXAGON: generated {} full hexagons, {} '
                          'boundary hexagons.'.format(full_count, partial_count))

        except Exception:
            if _ui:
                _ui.messageBox('Error generating the honeycomb geometry:\n{}'.format(
                    traceback.format_exc()))


class HoneycombPreviewHandler(adsk.core.CommandEventHandler):
    """Live preview: re-generates the honeycomb pattern every time an
    input changes, so the user can see the result before committing.
    Fusion automatically rolls back everything created here before the
    next preview refresh (or on Cancel) - the same mechanism used by
    built-in commands like Extrude. If the user clicks OK, the regular
    execute event creates the final, permanent geometry."""

    def __init__(self, active_sketch):
        super().__init__()
        self._active_sketch = active_sketch

    def notify(self, args):
        try:
            design = adsk.fusion.Design.cast(_app.activeProduct)
            current_edit_sketch = adsk.fusion.Sketch.cast(design.activeEditObject) \
                if design else None
            if current_edit_sketch is None or \
                    current_edit_sketch.entityToken != self._active_sketch.entityToken:
                return  # nothing sensible to preview outside the sketch

            sketch = self._active_sketch
            cmd_inputs = args.command.commandInputs

            params = _try_collect_generation_params(cmd_inputs, sketch)
            if params is None:
                # Input state not complete yet (e.g. no boundary selected,
                # or size is 0) - simply show no preview, no error message.
                return

            _generate_honeycomb(
                sketch, params['boundary_polygon'], params['edge_sources'],
                radius=params['hex_radius_cm'], gap=params['gap_cm'],
                orientation=params['orientation'])

            # Tells Fusion the preview geometry is valid and can be shown.
            # Since this creates the exact same geometry the execute event
            # would create, Fusion can reuse it directly if the user clicks
            # OK without changing any further inputs.
            args.isValidResult = True

        except Exception:
            # Never surface a message box while the user is still
            # interactively adjusting parameters - just show no preview
            # for this particular input state.
            pass


class HoneycombDestroyHandler(adsk.core.CommandEventHandler):
    """Fires when the command dialog closes for any reason (OK, Cancel,
    or the user closing it). Used here to restore the sketch's original
    'Show Points' setting, since it is temporarily turned off while this
    command is active (see HoneycombCommandCreatedHandler)."""

    def __init__(self, active_sketch, points_were_shown):
        super().__init__()
        self._active_sketch = active_sketch
        self._points_were_shown = points_were_shown

    def notify(self, args):
        try:
            if self._active_sketch and self._active_sketch.isValid:
                self._active_sketch.arePointsShown = self._points_were_shown
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Add-in entry points
# ---------------------------------------------------------------------------

def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        cmd_defs = _ui.commandDefinitions
        existing = cmd_defs.itemById(_CMD_ID)
        if existing:
            existing.deleteMe()

        if os.path.isdir(_RESOURCE_FOLDER):
            cmd_def = cmd_defs.addButtonDefinition(
                _CMD_ID, _CMD_NAME, _CMD_TOOLTIP, _RESOURCE_FOLDER)
        else:
            cmd_def = cmd_defs.addButtonDefinition(
                _CMD_ID, _CMD_NAME, _CMD_TOOLTIP)

        on_command_created = HoneycombCommandCreatedHandler()
        cmd_def.commandCreated.add(on_command_created)
        _handlers.append(on_command_created)

        panel = _ui.allToolbarPanels.itemById(_PANEL_ID)
        if panel:
            control = panel.controls.itemById(_CMD_ID)
            if not control:
                panel.controls.addCommand(cmd_def)

        _ui.messageBox(
            'NEXTGEN HEXAGON loaded successfully.\n\n'
            'Where to find it: edit a sketch, then look for the '
            '"NEXTGEN HEXAGON" button in the Create panel of the sketch '
            'toolbar.',
            'NEXTGEN HEXAGON')

    except Exception:
        if _ui:
            _ui.messageBox('Error starting the add-in:\n{}'.format(
                traceback.format_exc()))


def stop(context):
    try:
        panel = _ui.allToolbarPanels.itemById(_PANEL_ID)
        if panel:
            control = panel.controls.itemById(_CMD_ID)
            if control:
                control.deleteMe()

        cmd_def = _ui.commandDefinitions.itemById(_CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()

        _handlers.clear()
    except Exception:
        if _ui:
            _ui.messageBox('Error stopping the add-in:\n{}'.format(
                traceback.format_exc()))
