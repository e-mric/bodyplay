# 3D assets

Source models for the controller pictures in the drill and live windows
(`pc/bodyctl/assets/pads/*.png`; button hotspots in `pc/bodyctl/tools/pad_view.py`). Each
window shows the model's credit under the picture.

| Folder | Model | Author | Licence | In git |
|---|---|---|---|---|
| `xbox-inalambric-controller-white/` | ["Xbox Inalambric Controller (White)"](https://sketchfab.com/3d-models/xbox-inalambric-controller-white-f18a70fc10414ef5a39b55de68f12823) | Chistodrako._. (Sketchfab) | [CC BY 4.0](http://creativecommons.org/licenses/by/4.0/) | yes |
| `playstation-5-dualsense/` | ["Playstation 5 Dualsense"](https://skfb.ly/opKOx) | AHarmlessPotato (Sketchfab) | [CC BY 4.0](http://creativecommons.org/licenses/by/4.0/) | no: 92 MB, kept local (`.gitignore`) |

Changes: each model was rendered once to a still image (pyvista + trimesh, base-colour
textures only; the Xbox one from 40 degrees above its face, the DualSense from the front),
cropped and scaled to 760 px wide. The models themselves are unchanged.
