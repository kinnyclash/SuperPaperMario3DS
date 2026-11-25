# GX to PICA200 Translation Layer: Design

Status: design and reference model. The TEV reference model is implemented and tested in
`spm3ds/gx/tev.py`. No translation code exists yet.

## 1. What has to be translated

The Wii draws with GX, a fixed-function pipeline on the Hollywood GPU. The New 3DS uses the
PICA200, which has programmable vertex shaders and a fixed-function fragment stage that
the 3DS SDK exposes through citro3d. The translation layer turns the GX state the game sets
into PICA200 state that gives the same picture.

| GX area | What the game sets | PICA200 target | Difficulty |
|---|---|---|---|
| TEV stages | Colour and alpha combiners (up to 16 stages) | Texture environment stages (citro3d TexEnv, up to 6) | High: more stages than the hardware offers |
| Texture coordinate generation | Sources, matrices, emboss | Vertex shader computes texcoords | Medium |
| Textures | GX formats (I4, I8, IA4, IA8, RGB565, RGB5A3, RGBA8, CMPR) | PICA formats; CMPR decoded on load | Medium: CMPR and RGB5A3 need conversion |
| Vertex formats | Attribute formats and indexing (direct or indexed, compressed) | Vertex buffer layout, decoded on the CPU or in a shader | Medium |
| Transforms | Position, normal and texture matrices | Vertex shader uniforms | Low |
| Lighting | GX channels and lights | Vertex shader lighting, or fragment shader | Medium |
| Blending and alpha test | Blend equations, alpha compare, Z mode | PICA blend, alpha test and depth state | Low to medium |
| Framebuffer copies (EFB) | Copy from the framebuffer into a texture (XFB and effects) | Render-target-to-texture copy | High |
| Viewport and resolution | 640×480-class output | 400×240 top screen, scaled | Low in principle, visible in practice |

## 2. Layers

1. **Command capture.** The game calls GX functions from its own code, which the DOL contains.
   Three options, in order of preference for the first milestone:
   - Replace high-level GX functions by symbol name (HLE, through `spm3ds/hle.py` and the
     symbol file). This is the cheapest path, and it needs the function names from the
     user's own symbol work.
   - Trap writes to the GX FIFO, which is the lower-level route taken by emulators.
   - Recompile the GX register writes as they are, then translate the register state. This
     is the most faithful and the most work.
2. **State model.** A host-side structure holds the GX state (TEV, texgen, vertex, blend).
   It is independent of rendering, so it can be unit-tested.
3. **Translators.** One per area in the table above, each a pure function from state to
   PICA200 parameters. Each is tested against the GX reference in `spm3ds/gx`.
4. **Renderer.** Calls citro3d with the translated state. Only this layer touches the 3DS SDK.

## 3. TEV reference and the PICA mapping

The GX colour combiner computes, per channel:

    value = (d + lerp(a, b, c)) with bias and scale, clamped to [0, 1]

`spm3ds/gx/tev.py` implements this exactly, with `subtract`, bias and scale as options. The
constants are reproduced from public GX and Gekko references and must be checked against
the GX documentation before they are relied on for game output.

PICA200 offers fewer combiner stages than GX allows. Two approaches:

- **Direct mapping** for games that use up to six stages. Each GX TEV stage becomes one
  citro3d texture environment stage, with the same inputs and operations.
- **Shader generation** for stages beyond the hardware count, or for combinations that
  the fixed stages cannot express. Several GX stages are compiled into one fragment shader.
  This needs the shader path that the 3DS SDK exposes, and it is a later milestone.

Tests will compare each translated stage with the reference model over a grid of inputs.

## 4. Textures

GX texture formats are converted to the nearest PICA format on load:

- I4, I8, IA4, IA8 map to PICA luminance and alpha formats where they exist, or expand to RGBA8.
- RGB565 and RGBA8 map directly.
- RGB5A3 expands to RGBA8 (or RGBA4 where the precision is acceptable).
- CMPR (S3TC-style) is decoded to RGBA8 at load time. It is a fixed block format and the
  decoder can be tested against hand-computed blocks.

Each conversion is a pure function with its own tests. Texture memory is bounded by the
3DS budget (see the memory map in PROGRESS.md), so conversions happen on demand and the
results are cached.

## 5. Vertices

GX vertex attributes can be direct or indexed, and can use compressed formats. The
translator decodes them into a PICA vertex buffer layout. Indexed attributes are resolved
during conversion, so the GPU sees a flat buffer. Matrices are uploaded as shader uniforms.

## 6. Resolution and output

The Wii renders at a 480-line class resolution. The 3DS top screen is 400×240. The layer
chooses a viewport and scale per game, and handles the bottom screen as a separate target
(menus, maps) in later milestones.

## 7. Framebuffer copies

GX copies the framebuffer into textures for effects. On PICA200 this is a render-target-to-
texture copy. It is one of the largest risks in the project, and the first milestone avoids
it by stubbing the effect and logging the call.

## 8. Milestones

1. Clear screen and draw one textured quad through the translator (no TEV beyond a
   single stage). Validation: pixel comparison against the reference model.
2. Full TEV for up to six stages. Validation: the grid test against `tev.py`.
3. Texture formats and vertex formats. Validation: unit tests on each conversion.
4. Lighting, fog, blend and alpha test.
5. Framebuffer copies and effects.
6. Shader generation for stages beyond six.

## 9. Things to verify before implementing

- The GX combiner constants and clamping rules, against the GX documentation.
- The citro3d function names and signatures for texture environments, blending and alpha
  test. These are written from memory and must be checked against the installed headers.
- The PICA200 limits (texture units, stages, shader size) on the target hardware.
