#!/usr/bin/env python
import argparse
import os
import logging
import shutil
import re
from pathlib import Path
from typing import Union

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

LIBXR_CMAKE_TEMPLATE = (
'''set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# LibXR
if(NOT DEFINED LIBXR_SYSTEM)
    set(LIBXR_SYSTEM None)
endif()
set(LIBXR_DRIVER st)
set(XROBOT_MODULES_DIR ${CMAKE_CURRENT_SOURCE_DIR}/Modules)
if(NOT DEFINED LIBXR_SOURCE_DIR)
    set(LIBXR_SOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/Middlewares/Third_Party/LibXR)
endif()
if(NOT DEFINED LIBXR_BINARY_DIR)
    set(LIBXR_BINARY_DIR ${CMAKE_CURRENT_BINARY_DIR}/LibXR-build)
endif()
add_subdirectory(${LIBXR_SOURCE_DIR} ${LIBXR_BINARY_DIR})
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

LIBXR_SUBDIRECTORY_SETUP = '''if(NOT DEFINED LIBXR_SOURCE_DIR)
    set(LIBXR_SOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/Middlewares/Third_Party/LibXR)
endif()
if(NOT DEFINED LIBXR_BINARY_DIR)
    set(LIBXR_BINARY_DIR ${CMAKE_CURRENT_BINARY_DIR}/LibXR-build)
endif()
add_subdirectory(${LIBXR_SOURCE_DIR} ${LIBXR_BINARY_DIR})'''

LIBXR_SYSTEM_SETUP = '''if(NOT DEFINED LIBXR_SYSTEM)
    set(LIBXR_SYSTEM None)
endif()'''

def normalize_libxr_cmake(content: str) -> str:
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

    guarded_system_pattern = re.compile(
        r'^\s*if\s*\(\s*NOT\s+DEFINED\s+LIBXR_SYSTEM\s*\)\s*\n'
        r'\s*set\s*\(\s*LIBXR_SYSTEM\s+\S+\s*\)\s*\n'
        r'\s*endif\s*\(\s*\)\s*\n?',
        re.MULTILINE,
    )
    if guarded_system_pattern.search(content):
        content = guarded_system_pattern.sub(LIBXR_SYSTEM_SETUP + "\n", content, count=1)
    elif re.search(r'^\s*set\s*\(\s*LIBXR_SYSTEM\s+\S+\s*\)', content, re.MULTILINE):
        content = re.sub(
            r'^\s*set\s*\(\s*LIBXR_SYSTEM\s+\S+\s*\)\s*\n?',
            LIBXR_SYSTEM_SETUP + "\n",
            content,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        content = re.sub(
            r'(^\s*set\s*\(\s*LIBXR_DRIVER\s+\S+\s*\)\s*$)',
            LIBXR_SYSTEM_SETUP + "\n\\1",
            content,
            count=1,
            flags=re.MULTILINE
        )

    content = re.sub(
        r"if\(NOT DEFINED LIBXR_SOURCE_DIR\)[\s\S]*?"
        r"add_subdirectory\(\$\{LIBXR_SOURCE_DIR\}\s+\$\{LIBXR_BINARY_DIR\}\)",
        LIBXR_SUBDIRECTORY_SETUP,
        content,
        count=1,
    )
    content = re.sub(
        r"add_subdirectory\(\s*(?:\.\./)?Middlewares/Third_Party/LibXR(?:\s+[^)]+)?\)",
        LIBXR_SUBDIRECTORY_SETUP,
        content,
        count=1,
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


def update_or_create_libxr_cmake(
    file_path: str,
) -> None:
    cmake_path = Path(file_path)

    if cmake_path.exists():
        content = read_text_with_fallback(str(cmake_path))
        new_content = normalize_libxr_cmake(content)
        if new_content != content:
            cmake_path.write_text(new_content, encoding="utf-8")
            logging.info("Updated existing shared LibXR.CMake.")
        else:
            logging.info("LibXR.CMake already up to date, no changes needed.")
    else:
        cmake_path.write_text(
            LIBXR_CMAKE_TEMPLATE,
            encoding="utf-8"
        )
        logging.info(f"Generated LibXR.CMake at: {cmake_path}")


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


def project_root_for(input_directory: Path) -> Path:
    input_directory = input_directory.resolve()
    if (input_directory / "Middlewares" / "Third_Party" / "LibXR").is_dir():
        return input_directory
    parent = input_directory.parent
    if (parent / "Middlewares" / "Third_Party" / "LibXR").is_dir():
        return parent
    return input_directory


def cmake_include_block(input_directory: Path, project_root: Path, system: str) -> str:
    relative_root = os.path.relpath(project_root, input_directory).replace(os.sep, "/")
    prefix = "" if relative_root == "." else f"{relative_root}/"
    return (
        f"set(LIBXR_SYSTEM {system})\n"
        f"set(LIBXR_SOURCE_DIR \"${{CMAKE_CURRENT_LIST_DIR}}/{prefix}Middlewares/Third_Party/LibXR\")\n"
        f"include(${{CMAKE_CURRENT_LIST_DIR}}/{prefix}cmake/LibXR.CMake)\n"
    )


def update_cmake_include(input_directory: Path, project_root: Path, system: str) -> None:
    main_cmake_path = input_directory / "CMakeLists.txt"
    if not main_cmake_path.exists():
        logging.error("CMakeLists.txt not found.")
        exit(1)
    content = read_text_with_fallback(str(main_cmake_path))
    block = cmake_include_block(input_directory, project_root, system)
    include_pattern = re.compile(
        r"(?ms)^# Add LibXR\s*\n(?:set\(LIBXR_SYSTEM[^\n]*\)\s*\n)?"
        r"(?:set\(LIBXR_SOURCE_DIR[^\n]*\)\s*\n)?"
        r"include\(\$\{CMAKE_CURRENT_LIST_DIR\}/(?:\.\./)?cmake/LibXR\.CMake\)\s*\n?"
    )
    if include_pattern.search(content):
        new_content = include_pattern.sub("# Add LibXR\n" + block, content, count=1)
    else:
        new_content = content.rstrip() + "\n\n# Add LibXR\n" + block
    if new_content != content:
        main_cmake_path.write_text(new_content, encoding="utf-8", newline="\n")
        logging.info("LibXR.CMake shared include updated.")
    else:
        logging.info("LibXR.CMake shared include already up to date, no changes needed.")


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

    input_path = Path(input_directory).resolve()
    project_root = project_root_for(input_path)
    cmake_dir = project_root / "cmake"
    os.makedirs(cmake_dir, exist_ok=True)

    file_path = cmake_dir / "LibXR.CMake"

    freertos_enable = os.path.exists(os.path.join(input_directory, "Core", "Inc", "FreeRTOSConfig.h"))
    threadx_enable = os.path.exists(os.path.join(input_directory, "Core", "Inc", "app_threadx.h"))

    if freertos_enable:
        system = "FreeRTOS"
    elif threadx_enable:
        system = "ThreadX"
    else:
        system = "None"

    update_or_create_libxr_cmake(file_path)
    logging.info("LibXR.CMake generated/updated successfully.")
    update_cmake_include(input_path, project_root, system)
