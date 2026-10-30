# Drake Diagram Viewer

Interactive viewer for [Drake](https://drake.mit.edu) system diagrams. It takes the static Graphviz SVG that Drake exports and rebuilds it as a live, explorable scene: click a block to trace its connections, fold sub-diagrams, and group blocks.

Large Drake diagrams quickly turn into a wall of boxes and wires. This tool makes them navigable.

> **Note:** This project is fully vibe coded and is delivered as-is. See [Disclaimer](#disclaimer).

[![Drake Diagram Viewer in dark mode](https://github.com/pnagels2004/DrakeMap/raw/main/example/dark_diagram.png)](https://github.com/pnagels2004/DrakeMap/blob/main/example/dark_diagram.png)

## Features

- **Click to trace**: selecting a block highlights all its connections (orange = outgoing, blue = incoming, green = between selected blocks). Connected port rows light up and everything unrelated is dimmed.
- **Drag blocks**: arrows follow, diagram boxes resize to fit. Dragging a diagram's title or border moves the whole sub-diagram.
- **Fold / unfold sub-diagrams**: collapse a sub-diagram into a single block that lists its input and output ports. Arrows re-attach to the matching port rows.
- **Groups**: group several blocks, move them as one, rename the group.
- **Search**: find blocks by name or type; diagrams hiding a match are unfolded automatically.
- **Save layout**: positions, groups and folds are stored next to the SVG (`Ctrl+S`) and restored the next time you open it.
- **Auto-reload**: when the SVG is regenerated (e.g. by your own script), the viewer reloads it by itself. The layout is reset to the new SVG's own positions (folds and groups are cleared too), but the highlighted blocks stay highlighted and the zoom is kept.
- **Values inspector** *(optional)*: a dock on the right lists the input and output values of the selected block(s), fed live by `drake_probe.py` while your simulation runs.
- **Neovim link** *(optional)*: the block whose name matches the word under your Neovim cursor is selected and the view glides to it (via `drake_cursor.lua`).
- **Export**: render to PNG or SVG, also headless from the command line.
- **Dark mode** by default, toggleable and remembered.

## Screenshots

**Click a block to trace its connections.** The selected block gets a yellow outline, outgoing arrows turn orange, incoming arrows turn blue, and everything unrelated is dimmed.

[![Selecting a block highlights its connections](https://github.com/pnagels2004/DrakeMap/raw/main/example/selection.png)](https://github.com/pnagels2004/DrakeMap/blob/main/example/selection.png)

**Light mode** is available too (`Ctrl+Shift+D`).

[![Drake Diagram Viewer in light mode](https://github.com/pnagels2004/DrakeMap/raw/main/example/light_diagram.png)](https://github.com/pnagels2004/DrakeMap/blob/main/example/light_diagram.png)

## Installation

Requires Python 3.9+ and [PySide6](https://pypi.org/project/PySide6/). PyQt6 can run the interactive viewer and PNG export, but SVG export currently needs PySide6.

```
git clone https://github.com/pnagels2004/DrakeMap.git
cd DrakeMap
pip install PySide6
```

To export diagrams from your own Drake scripts (step 1 below) you also need:

```
pip install pydot
```

and the [Graphviz](https://graphviz.org/download/) `dot` binary on your system (e.g. `sudo dnf install graphviz` on Fedora, `sudo apt install graphviz` on Debian/Ubuntu).

## Usage

### 1. Export your diagram from Drake

```python
import pydot

# `diagram` is your built Drake Diagram (e.g. builder.Build())
# Graphviz string with diagram depth 10
graph = pydot.graph_from_dot_data(
    diagram.GetGraphvizString(max_depth=10))[0]

# write it to the svg
graph.write_svg("system_diagram.svg")
```

### 2. Open it

```
python visualizer.py system_diagram.svg
```

Or run it without an SVG and it will ask for one on startup (`Ctrl+O` opens another file later).

### Headless export

```
python visualizer.py system_diagram.svg --export out.png
python visualizer.py system_diagram.svg --collapse --export out.png
```

Other options: `--layout FILE`, `--theme dark|light`, `--scale N`, `--values FILE` (values JSON written by `drake_probe.py`), `--cursor FILE` (cursor file written by `drake_cursor.lua`).

## Controls

| Action                        | Effect                               |
|-------------------------------|--------------------------------------|
| Click block                   | Highlight block and all its arrows   |
| Drag block                    | Move it; arrows follow               |
| Drag diagram title/border     | Move the whole sub-diagram           |
| `[-]` / `[+]` or double-click | Fold / unfold a sub-diagram          |
| Drag on empty space           | Rubber-band select                   |
| Shift + click                 | Add to selection                     |
| Alt + click                   | Select one block, ignoring its group |
| `Ctrl+G` / `Ctrl+Shift+G`     | Group / ungroup selection            |
| `Ctrl+K` / `Ctrl+Shift+K`     | Fold all / unfold all                |
| `Ctrl+F`                      | Search blocks                        |
| `D`                           | Toggle dimming of unrelated items    |
| `F`                           | Fit to window                        |
| Ctrl + drag, right/middle drag | Pan                                 |
| Wheel                         | Zoom                                 |
| `Ctrl+O`                      | Open an SVG                          |
| `Ctrl+S`                      | Save layout                          |
| `Ctrl+E`                      | Export PNG / SVG                     |
| `F5`                          | Force reload                         |
| `Ctrl+I`                      | Show / hide the values inspector     |
| `Ctrl+Shift+N`                | Follow the Neovim cursor on / off    |
| `Ctrl+Shift+D`                | Dark / light mode                    |
| `F1`                          | Show controls                        |

## Optional integrations

These helpers are independent of the viewer itself. Both write small JSON files to `~/.cache/drake_diagram_viewer/` (or under `$XDG_CACHE_HOME`), which the viewer reads.

### `drake_probe.py`: live port values

Replaces `sim.AdvanceTo(np.inf)` while debugging. After every chunk of simulated time it evaluates the input and output ports of every system (recursively) and writes a compact summary to `~/.cache/drake_diagram_viewer/<name>.values.json`. The viewer shows these values in its values inspector (`Ctrl+I`) for the selected block(s); use `--values FILE` if you keep the file elsewhere.

```python
from drake_probe import run_debug

sim = Simulator(diagram, context)
sim.Initialize()
run_debug(sim, diagram, "system_diagram")   # name = stem of your SVG
```

Useful options: `period` (simulated seconds between snapshots), `skip` (systems whose ports must not be evaluated, e.g. a camera), `end_time`.

### `drake_cursor.lua`: editor link

A Neovim plugin that writes the word under the cursor (in Python files) to `~/.cache/drake_diagram_viewer/nvim_cursor.json`, so the viewer can select the block with that name (or, if no block has that name, the blocks of that class, e.g. `Gain`). A word that matches nothing leaves the selection unchanged. Toggle the plugin with `:DrakeCursorToggle`, and the viewer's side of the link with `Ctrl+Shift+N` (`--cursor FILE` overrides the path).

It is written for [AstroNvim](https://astronvim.com) (it uses `AstroNvim/astrocore` options). Copy `drake_cursor.lua` to `~/.config/nvim/lua/plugins/`. For other Neovim setups, adapt the autocmd and command registration.

## How it works

The SVG is parsed into blocks (systems), arrows (connections) and diagram boxes (clusters), then rebuilt as a Qt `QGraphicsScene`. Blocks get stable signatures (enclosing diagram names + block type + block name), so layouts survive regenerated SVGs even when Graphviz changes its internal node ids.

## Limitations

- Built for the SVG layout produced by Drake's Graphviz export. Other Graphviz SVGs may not parse correctly.
- Tested on Linux (Fedora) only. The cache location above follows the Linux convention and has not been checked on Windows or macOS.

## Disclaimer

This project is **fully vibe coded**: written through conversation with an AI assistant, not through traditional hand-written software engineering. It is delivered **as-is**, with no guarantee of correctness, stability, or fitness for any purpose, and no promise of maintenance or support. Expect rough edges and bugs. Issues and pull requests are welcome, but responses may be slow or absent.

This project is not affiliated with or endorsed by the Drake project, Toyota Research Institute, or MIT.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
