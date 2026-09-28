#!/usr/bin/env python
import argparse
import os
import logging
import shutil
import re
from pathlib import Path
from typing import Union

from xr_syntax.cpp import CppDocument

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

LIBXR_CMAKE_TEMPLATE = (
'''set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# LibXR
set(LIBXR_SYSTEM _LIBXR_SYSTEM_)
set(LIBXR_DRIVER st)
_XROBOT_MODULES_DIR_add_subdirectory(Middlewares/Third_Party/LibXR)
target_link_libraries(xr
    PUBLIC stm32cubemx
)
target_compile_features(xr PUBLIC cxx_std_20)

set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES
    CXX_STANDARD 20
    CXX_STANDARD_REQUIRED ON
)

target_include_directories(xr
    PUBLIC $<TARGET_PROPERTY:stm32cubemx,INTERFACE_INCLUDE_DIRECTORIES>
    PUBLIC Core/Inc
    PUBLIC User
)

# Add include paths
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined include paths
    PUBLIC $<TARGET_PROPERTY:xr,INTERFACE_INCLUDE_DIRECTORIES>
    PUBLIC User
)

# Add linked libraries
target_link_libraries(${CMAKE_PROJECT_NAME}
    stm32cubemx

    # Add user defined libraries
    xr
)

file(
    GLOB LIBXR_USER_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/User/*.cpp")


target_sources(${CMAKE_PROJECT_NAME}
    PRIVATE ${LIBXR_USER_SOURCES}
)

if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    target_compile_options(${CMAKE_PROJECT_NAME} PRIVATE -Og)
    target_compile_options(xr PRIVATE -O2)
    if(TARGET FreeRTOS)
        target_compile_options(FreeRTOS PRIVATE -O2)
    endif()

    if(TARGET STM32_Drivers)
        target_compile_options(STM32_Drivers PRIVATE -O2)
    endif()

    if(TARGET USB_Device_Library)
        target_compile_options(USB_Device_Library PRIVATE -O2)
    endif()
endif()
'''
)

XROBOT_MODULES_DIR_LINE = "set(XROBOT_MODULES_DIR ${CMAKE_CURRENT_SOURCE_DIR}/Modules)\n"

include_cmake_cmd = "include(${CMAKE_CURRENT_LIST_DIR}/cmake/LibXR.CMake)\n"


def project_uses_xrobot(input_directory: str) -> bool:
    """Report whether User/app_main.cpp was generated with --xrobot.

    xr_gen_code_stm32 --xrobot includes xrobot_main.hpp outside the User Code
    regions; xr_cubemx_cfg generates that file before the CMake integration.
    """
    app_main = os.path.join(input_directory, "User", "app_main.cpp")
    if not os.path.isfile(app_main):
        return False
    document = CppDocument.parse(read_text_with_fallback(app_main))
    regions = [region.body_span for region in document.user_regions()]
    for include in document.include_views():
        start = include.node.span.start
        if include.header == "xrobot_main.hpp" and not any(
                span.start <= start < span.end for span in regions):
            return True
    return False


def normalize_libxr_cmake(content: str, system: str) -> str:
    content = re.sub(
        r'^\s*set\s*\(\s*CMAKE_CXX_STANDARD\s+\d+\s*\)\s*\n?',
        '',
        content,
        flags=re.MULTILINE
    )
    content = re.sub(
        r'^\s*set\s*\(\s*CMAKE_CXX_STANDARD_REQUIRED\s+\S+\s*\)\s*\n?',
        '',
        content,
        flags=re.MULTILINE
    )
    content = "set(CMAKE_CXX_STANDARD 20)\nset(CMAKE_CXX_STANDARD_REQUIRED ON)\n\n" + content.lstrip('\n')

    system_pattern = re.compile(
        r'(^\s*set\s*\(\s*LIBXR_SYSTEM\s+)(\S+)(\s*\)\s*)',
        re.MULTILINE
    )
    if system_pattern.search(content):
        content = system_pattern.sub(rf'\1{system}\3', content, count=1)
    else:
        content = re.sub(
            r'(^\s*set\s*\(\s*LIBXR_DRIVER\s+\S+\s*\)\s*$)',
            f"set(LIBXR_SYSTEM {system})\n\\1",
            content,
            count=1,
            flags=re.MULTILINE
        )

    content = re.sub(
        r'target_compile_features\s*\(\s*xr\s+PUBLIC\s+cxx_std_\d+\s*\)',
        'target_compile_features(xr PUBLIC cxx_std_20)',
        content,
        count=1
    )
    if "target_compile_features(xr PUBLIC cxx_std_20)" not in content:
        content = re.sub(
            r'(target_link_libraries\s*\(\s*xr\b[\s\S]*?\)\s*)',
            r'\1\ntarget_compile_features(xr PUBLIC cxx_std_20)\n',
            content,
            count=1
        )

    if "set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES" not in content:
        content = re.sub(
            r'(^\s*target_include_directories\(\$\{CMAKE_PROJECT_NAME\}\s+PRIVATE\s*$)',
            "set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES\n"
            "    CXX_STANDARD 20\n"
            "    CXX_STANDARD_REQUIRED ON\n"
            ")\n\n"
            r'\1',
            content,
            count=1,
            flags=re.MULTILINE
        )

    return content


def update_or_create_libxr_cmake(file_path: str, system: str, use_xrobot: bool) -> None:
    cmake_path = Path(file_path)

    if cmake_path.exists():
        content = read_text_with_fallback(str(cmake_path))
        new_content = normalize_libxr_cmake(content, system)
        if new_content != content:
            cmake_path.write_text(new_content, encoding="utf-8")
            logging.info(f"Updated existing LibXR.CMake for system: {system}")
        else:
            logging.info("LibXR.CMake already up to date, no changes needed.")
        # The existing file is user-owned here; report a mismatch only.
        declares_modules = re.search(r'^\s*set\s*\(\s*XROBOT_MODULES_DIR\b', new_content,
                                     flags=re.MULTILINE) is not None
        if declares_modules and not use_xrobot:
            logging.warning("LibXR.CMake sets XROBOT_MODULES_DIR, but User/app_main.cpp was not "
                            "generated with --xrobot; remove that line for a LibXR-only project.")
        elif use_xrobot and not declares_modules:
            logging.warning("User/app_main.cpp uses XRobot, but LibXR.CMake does not set "
                            "XROBOT_MODULES_DIR; add: " + XROBOT_MODULES_DIR_LINE.strip())
    else:
        # XRobot projects build their Modules through LibXR; plain LibXR
        # projects must not name a Modules directory.
        cmake_path.write_text(
            LIBXR_CMAKE_TEMPLATE.replace("_LIBXR_SYSTEM_", system).replace(
                "_XROBOT_MODULES_DIR_", XROBOT_MODULES_DIR_LINE if use_xrobot else ""),
            encoding="utf-8"
        )
        logging.info(f"Generated LibXR.CMake at: {cmake_path}")


# Inserted after CubeMX's set(STARM_TOOLCHAIN_CONFIG "<default>") line, which
# stays the only place that names the default profile.
STARM_PROFILE_SELECTION = '''
# LibXR: -DSTARM_TOOLCHAIN_CONFIG=<profile> selects the profile of a build
# directory; a default changed by xr_stm32_toolchain_switch also reaches
# existing build directories.
set(_xr_starm_default ${STARM_TOOLCHAIN_CONFIG})
unset(STARM_TOOLCHAIN_CONFIG)
if(NOT DEFINED CACHE{STARM_TOOLCHAIN_CONFIG} OR
   (DEFINED CACHE{XR_STARM_TOOLCHAIN_DEFAULT} AND
    NOT XR_STARM_TOOLCHAIN_DEFAULT STREQUAL _xr_starm_default))
  set(STARM_TOOLCHAIN_CONFIG ${_xr_starm_default} CACHE STRING "ST Arm Clang runtime profile" FORCE)
endif()
set(XR_STARM_TOOLCHAIN_DEFAULT ${_xr_starm_default} CACHE INTERNAL "Default STARM_TOOLCHAIN_CONFIG of this file")
set_property(CACHE STARM_TOOLCHAIN_CONFIG PROPERTY STRINGS STARM_HYBRID STARM_NEWLIB STARM_PICOLIBC)'''


def normalize_starm_clang_toolchain(file_path: Union[str, Path]) -> None:
    """Make CubeMX's ST Arm Clang runtime profile selectable per build directory.

    xr_stm32_toolchain_switch selects the default profile by rewriting
    set(STARM_TOOLCHAIN_CONFIG "...") in this file, and a build may select
    another one with -DSTARM_TOOLCHAIN_CONFIG=... . Earlier versions turned
    the default line itself into a CACHE STRING: a cache entry is only
    initialized by the first configure of a build directory, so the switch
    never reached an existing build tree. The default stays a plain line; the
    inserted selection block keeps the profile in the cache and replaces it
    when the file default differs from the one recorded at the last
    configure (the toolchain file is re-read on every configure).
    """
    path = Path(file_path)
    if not path.exists():
        return

    content = read_text_with_fallback(str(path))
    line_pattern = re.compile(
        r'^(\s*set\s*\(\s*STARM_TOOLCHAIN_CONFIG\s+")([^"]+)'
        r'"(?:\s+CACHE\s+STRING\s+"[^"]*")?\s*\)[ \t]*$'
        # Property list written by earlier versions after a CACHE default.
        r'(?:\nset_property\s*\(\s*CACHE\s+STARM_TOOLCHAIN_CONFIG\s+PROPERTY\s+STRINGS'
        r'\s+STARM_HYBRID\s+STARM_NEWLIB\s+STARM_PICOLIBC\s*\))?',
        re.MULTILINE,
    )
    match = line_pattern.search(content)
    if match is None:
        logging.warning("STARM_TOOLCHAIN_CONFIG not found in %s", path)
        return

    replacement = f'{match.group(1)}{match.group(2)}")'
    if STARM_PROFILE_SELECTION not in content:
        replacement += STARM_PROFILE_SELECTION
    new_content = content[:match.start()] + replacement + content[match.end():]

    # CubeMX emits an if/elseif block that computes multilib flags. Initialize the
    # variable explicitly and reject misspelled runtime profiles.
    if 'set(TOOLCHAIN_MULTILIBS "")' not in new_content:
        marker = 'if(STARM_TOOLCHAIN_CONFIG STREQUAL "STARM_HYBRID")'
        if marker in new_content:
            new_content = new_content.replace(
                marker, 'set(TOOLCHAIN_MULTILIBS "")\n\n' + marker, 1
            )

    first_if = new_content.find('if(STARM_TOOLCHAIN_CONFIG STREQUAL "STARM_HYBRID")')
    if first_if >= 0:
        first_endif = new_content.find('endif()', first_if)
        if first_endif >= 0:
            block = new_content[first_if:first_endif]
            guard = (
                'elseif(NOT STARM_TOOLCHAIN_CONFIG STREQUAL "STARM_PICOLIBC")\n'
                '  message(FATAL_ERROR "Unknown STARM_TOOLCHAIN_CONFIG: '
                '${STARM_TOOLCHAIN_CONFIG}")\n'
            )
            if 'Unknown STARM_TOOLCHAIN_CONFIG' not in block:
                new_content = new_content[:first_endif] + guard + new_content[first_endif:]

    if new_content != content:
        # Path.write_text(newline=...) is only available on newer Python. Keep
        # the package's Python 3.8 support while still emitting deterministic LF.
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(new_content)
        logging.info("Normalized STARM_TOOLCHAIN_CONFIG in %s", path)


def clean_cmake_build_dirs(input_directory: Union[str, Path]) -> None:
    input_directory = Path(input_directory)
    removed = False
    for d in input_directory.iterdir():
        if d.is_dir() and (d.name == "build" or d.name.startswith("cmake-build")):
            shutil.rmtree(d)
            logging.info(f"Removed {d}")
            removed = True
    if not removed:
        logging.info("No build or cmake-build* directory found, nothing to clean.")


def read_text_with_fallback(path: str) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return Path(path).read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return Path(path).read_text(encoding="utf-8")


def main():
    from libxr.PackageInfo import LibXRPackageInfo

    LibXRPackageInfo.check_and_print()

    parser = argparse.ArgumentParser(description="Generate CMake file for LibXR.")
    parser.add_argument("input_dir", type=str, help="CubeMX CMake Project Directory")

    args = parser.parse_args()
    input_directory = args.input_dir

    if not os.path.isdir(input_directory):
        logging.error("Input directory does not exist.")
        exit(1)

    clean_cmake_build_dirs(input_directory)

    cmake_dir = os.path.join(input_directory, "cmake")
    os.makedirs(cmake_dir, exist_ok=True)

    file_path = os.path.join(cmake_dir, "LibXR.CMake")

    freertos_enable = os.path.exists(os.path.join(input_directory, "Core", "Inc", "FreeRTOSConfig.h"))
    threadx_enable = os.path.exists(os.path.join(input_directory, "Core", "Inc", "app_threadx.h"))

    if freertos_enable:
        system = "FreeRTOS"
    elif threadx_enable:
        system = "ThreadX"
    else:
        system = "None"

    update_or_create_libxr_cmake(file_path, system, project_uses_xrobot(input_directory))
    logging.info("LibXR.CMake generated/updated successfully.")

    normalize_starm_clang_toolchain(os.path.join(cmake_dir, "starm-clang.cmake"))

    main_cmake_path = os.path.join(input_directory, "CMakeLists.txt")
    if os.path.exists(main_cmake_path):
        cmake_content = read_text_with_fallback(main_cmake_path)

        if include_cmake_cmd not in cmake_content:
            with open(main_cmake_path, "a", encoding="utf-8", newline="\n") as f:
                f.write('\n# Add LibXR\n' + include_cmake_cmd)
            logging.info("LibXR.CMake included in CMakeLists.txt.")
        else:
            logging.info("LibXR.CMake already included in CMakeLists.txt.")
    else:
        logging.error("CMakeLists.txt not found.")
        exit(1)
