# SPDX-License-Identifier: BSD-2-Clause
include_directories(${PROJECT_BINARY_DIR}/src/libcalamaresui)
calamares_add_plugin(bcachefstiering
    TYPE viewmodule
    EXPORT_MACRO PLUGINDLLEXPORT_PRO
    SOURCES BcachefsTieringViewStep.cpp
    SHARED_LIB
)
