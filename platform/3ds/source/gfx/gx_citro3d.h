/* citro3d rendering backend for the spm3ds graphics demo (APP=gfx). MIT License. */
#ifndef SPM3DS_GX_CITRO3D_H
#define SPM3DS_GX_CITRO3D_H

#include <3ds.h>
#include <citro3d.h>

/* Vertex layout read by the shader: v0 = position (3 floats), v1 = texture coordinate (2). */
typedef struct {
    float pos[3];
    float tc[2];
} gfx_vertex;

typedef struct {
    C3D_RenderTarget *target;
    shaderProgram_s program;
    DVLB_s *dvlb;
    C3D_Tex tex;
    C3D_AttrInfo attr;
    C3D_BufInfo buf;
    gfx_vertex *vbo;
    C3D_Mtx projection;
    s8 u_projection;
    s8 u_modelview;
} gfx_cube;

int gfx_init(void);      /* gfxInitDefault + C3D_Init; returns 0 on success */
void gfx_shutdown(void); /* C3D_Fini + gfxExit */

int gfx_cube_create(gfx_cube *cube); /* returns 0 on success, negative step code otherwise */
void gfx_cube_frame(gfx_cube *cube, float angle_x, float angle_y); /* one frame, top screen */
void gfx_cube_destroy(gfx_cube *cube);

#endif /* SPM3DS_GX_CITRO3D_H */
