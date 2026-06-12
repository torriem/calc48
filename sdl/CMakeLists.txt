# ---------------------------------------------------------------------------
# x48 -- the SDL 1.2 front end
# ---------------------------------------------------------------------------
# One client of libcalc48: it implements the hp48_ui_t rendering callbacks and
# (via the bundled stdio provider) drives loading/saving, then runs the
# emulator cooperatively with hp48_run_slice().

find_package(SDL REQUIRED)   # SDL 1.2 (FindSDL provides SDL::SDL)

add_executable(x48
    src/mainsdl.c
    src/x48_sdl.c
    src/SDL_gfxPrimitives.c
    src/SDL_rotozoom.c
)

# Front-end-private headers (x48_sdl.h, bitmaps/, ...) live alongside the .c.
target_include_directories(x48 PRIVATE src)
target_compile_definitions(x48 PRIVATE WITH_SDL ${PLATFORM_DEFINE})
if(UNIX AND NOT APPLE)
    target_compile_definitions(x48 PRIVATE _GNU_SOURCE)
endif()

# Linking the core brings in its public include directory (libcalc48/include).
target_link_libraries(x48 PRIVATE calc48)

# SDL (FindSDL exposes either the imported target or the classic variables).
if(TARGET SDL::SDL)
    target_link_libraries(x48 PRIVATE SDL::SDL)
else()
    target_include_directories(x48 PRIVATE ${SDL_INCLUDE_DIRS})
    target_link_libraries(x48 PRIVATE ${SDL_LIBRARIES})
endif()

# SDL_rotozoom / SDL_gfxPrimitives use sincos/ceil.
find_library(MATH_LIBRARY m)
if(MATH_LIBRARY)
    target_link_libraries(x48 PRIVATE ${MATH_LIBRARY})
endif()
