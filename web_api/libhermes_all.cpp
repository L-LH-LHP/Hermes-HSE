// Build Hermes client shared library as a single translation unit
// to avoid multiple-definition link errors.

#include "../hickae.hpp"
#include "hermes_client_api.cpp"
#include "document_storage.cpp"
