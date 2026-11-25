# New Nintendo 3DS / New 2DS XL build for spm3ds (devkitARM + libctru + citro3d).
#
# Two apps share this file:
#   make APP=cpu   -> spm3ds.3dsx      CPU demo: translated PowerPC, diagnostics, NDSP tone
#   make APP=gfx   -> spm3ds_gfx.3dsx  Graphics demo: textured cube (citro3d), and a cube drawn
#                                      by translated PowerPC code through GX (press A)
# Default: APP=cpu.
#
# Structure follows devkitPro's examples (3ds-examples/templates/application/Makefile and
# graphics/gpu/textured_cube/Makefile): include $(DEVKITARM)/3ds_rules, libctru headers and
# libraries from $(CTRULIB), citro3d from the same tree, picasso for .v.pica shaders.
#
# STATUS: UNVERIFIED ON HARDWARE. Paths and rules were checked against devkitPro's public
# examples and headers (see docs/API_VERIFICATION.md). Not yet compiled with devkitARM here.

ifeq ($(strip $(DEVKITPRO)),)
$(error "DEVKITPRO must be set (see https://devkitpro.org)")
endif
ifeq ($(strip $(DEVKITARM)),)
$(error "DEVKITARM must be set (it is set by the devkitARM install)")
endif

include $(DEVKITARM)/3ds_rules

APP ?= cpu
RUNTIME := ../../runtime

ifeq ($(APP),cpu)
  TARGET      := spm3ds
  SOURCES     := source/cpu source/generated
  LIBS        := -lctru -lm
  RUNTIME_SRC := $(RUNTIME)/hle.c $(RUNTIME)/os.c $(RUNTIME)/mem.c $(RUNTIME)/hw.c \
                 $(RUNTIME)/dvd.c $(RUNTIME)/os_thread.c
  SHADER_DIRS :=
else ifeq ($(APP),gfx)
  TARGET      := spm3ds_gfx
  SOURCES     := source/gfx
  LIBS        := -lcitro3d -lctru -lm
  RUNTIME_SRC := $(RUNTIME)/hw.c $(RUNTIME)/gx_cp.c $(RUNTIME)/gx_tex.c
  SHADER_DIRS := source/gfx
else
  $(error "APP must be cpu or gfx")
endif

BUILD    := build-$(APP)
INCLUDES := include
LIBDIRS  := $(CTRULIB)

# ---- code generation (same flags as the devkitPro examples) -----------------
ARCH := -march=armv6k -mtune=mpcore -mfloat-abi=hard -mtp=soft

# SPM3DS_DIAG_LEVEL=1: count guest instructions once per basic block (exact totals, cheap on
# the in-order ARM11); crash reports name the start of the block. See runtime/ppc_ctx.h.
DIAG_LEVEL ?= 1
CFLAGS  := -g -Wall -O2 -mword-relocations -ffunction-sections $(ARCH) -D__3DS__ -std=gnu11 \
           -MMD -MP -DSPM3DS_DIAG_LEVEL=$(DIAG_LEVEL)
CFLAGS  += $(foreach d,$(INCLUDES),-I$(CURDIR)/$(d)) \
           $(foreach d,$(LIBDIRS),-I$(d)/include) \
           -I$(RUNTIME) -I$(CURDIR)/$(BUILD)
# Extra flags from the command line, e.g. `make EXTRA_CFLAGS=-Werror` (CI uses this).
CFLAGS  += $(EXTRA_CFLAGS)
LDFLAGS := -specs=3dsx.specs -g $(ARCH) $(foreach d,$(LIBDIRS),-L$(d)/lib)

# ---- sources and objects ----------------------------------------------------
SRC_C := $(shell find $(SOURCES) -name '*.c') $(RUNTIME_SRC)
PICA  := $(foreach d,$(SHADER_DIRS),$(wildcard $(d)/*.v.pica))

# Objects mirror the source tree; "../" is rewritten so files outside the tree stay in $(BUILD).
OBJ_C      := $(addprefix $(BUILD)/,$(subst ../,up/,$(SRC_C:.c=.o)))
SHADER_O   := $(patsubst %.v.pica,$(BUILD)/%.shbin.o,$(notdir $(PICA)))
SHADER_H   := $(patsubst %.v.pica,$(BUILD)/%_shbin.h,$(notdir $(PICA)))
OBJ        := $(OBJ_C) $(addprefix $(BUILD)/,$(notdir $(SHADER_O)))
DEP        := $(OBJ_C:.o=.d)

ELF      := $(BUILD)/$(TARGET).elf
THREEDSX := $(TARGET).3dsx

vpath %.c $(SOURCES) $(RUNTIME)
vpath %.v.pica $(SHADER_DIRS)

.PHONY: all clean

all: $(THREEDSX)

$(THREEDSX): $(ELF)
	3dsxtool $< $@
	@echo "built $@"

$(ELF): $(OBJ)
	$(CC) $(LDFLAGS) -o $@ $(OBJ) $(LIBS)

# Compile C. Shader headers must exist before any object is built.
$(OBJ_C): | $(SHADER_H)

$(BUILD)/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/up/up/runtime/%.o: $(RUNTIME)/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

# ---- shaders: picasso assembles .v.pica, bin2s embeds the binary (citro3d test rule) ----
$(BUILD)/%.shbin: %.v.pica
	@mkdir -p $(dir $@)
	picasso -o $@ $<

# Header names follow bin2s: passthrough.shbin -> passthrough_shbin, passthrough_shbin_size.
$(BUILD)/%_shbin.h: $(BUILD)/%.shbin
	@echo "extern const u8" $(*F)_shbin_end"[];"  > $@
	@echo "extern const u8" $(*F)_shbin"[];"     >> $@
	@echo "extern const u32" $(*F)_shbin_size";" >> $@

# -a 4: DVLB_ParseFile reads the shader binary as u32 words, so it must be 4-byte aligned.
$(BUILD)/%.shbin.o: $(BUILD)/%.shbin $(BUILD)/%_shbin.h
	bin2s -a 4 $< | $(AS) -o $@

-include $(DEP)

clean:
	rm -rf build-cpu build-gfx spm3ds.3dsx spm3ds_gfx.3dsx spm3ds.elf spm3ds_gfx.elf
