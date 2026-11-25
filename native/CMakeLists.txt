cmake_minimum_required(VERSION 3.18)
project(PALSParserPyWheel LANGUAGES C CXX)
# Wheel DLLs must also load on hosts without the Visual Studio redistributable.
# Keep this private to wheels; conda controls its own compiler/runtime policy.
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>" CACHE STRING "" FORCE)
endif()
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(PALS_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(PALS_USE_SYSTEM_DEPS OFF CACHE BOOL "" FORCE)
set(FETCHCONTENT_FULLY_DISCONNECTED ON CACHE BOOL "" FORCE)
get_filename_component(VENDOR "${CMAKE_CURRENT_LIST_DIR}/../vendor" ABSOLUTE)
set(FETCHCONTENT_SOURCE_DIR_RAPIDYAML "${VENDOR}/rapidyaml" CACHE PATH "" FORCE)
set(FETCHCONTENT_SOURCE_DIR_PCRE2 "${VENDOR}/pcre2" CACHE PATH "" FORCE)
set(FETCHCONTENT_SOURCE_DIR_APC "${VENDOR}/apc" CACHE PATH "" FORCE)
add_subdirectory("${VENDOR}/PALSParserCpp" pals)
install(TARGETS PALSParserCpp
    LIBRARY DESTINATION palsparserpy/_native COMPONENT PythonWheel
    RUNTIME DESTINATION palsparserpy/_native COMPONENT PythonWheel)
# Ship the complete corresponding source in the sdist; retain all bundled notices.
file(GLOB_RECURSE NOTICES RELATIVE "${VENDOR}" "${VENDOR}/*LICENSE*" "${VENDOR}/*COPYING*")
foreach(NOTICE IN LISTS NOTICES)
    get_filename_component(SUBDIR "${NOTICE}" DIRECTORY)
    install(FILES "${VENDOR}/${NOTICE}"
        DESTINATION "palsparserpy/_native/licenses/${SUBDIR}" COMPONENT PythonWheel)
endforeach()
install(FILES "${CMAKE_CURRENT_LIST_DIR}/../native-lock.json"
    DESTINATION palsparserpy/_native COMPONENT PythonWheel)
