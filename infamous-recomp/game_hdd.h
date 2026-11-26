#pragma once

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>

extern "C" void cellfs_add_path_mapping(const char*, const char*);
extern "C" int sys_fs_default_hdd0;

/* Both launchers and the diagnostic tools run this native boot. Install data
 * belongs beside the working folder's saves on a fresh install. Older builds
 * cached the PSARCs at <disc root>/game/<title>; keep those caches in place. */
static bool configure_game_hdd(const char* disc_root, const char* title)
{
    namespace fs = std::filesystem;
    if (strcmp(title, "BCUS98119") && strcmp(title, "BCES00220"))
        return true;
    const char* configured = getenv("PS3_HDD0_ROOT");
    const bool use_default = !configured || !*configured;
    const fs::path usrdir = fs::path("game") / title / "USRDIR";
    fs::path root = use_default ? "gamedata/dev_hdd0" : configured;
    std::error_code error;
    if (use_default && !fs::is_directory(root / usrdir, error) &&
        fs::is_directory(fs::path(disc_root) / usrdir, error))
        root = disc_root;

    fs::create_directories(root / usrdir, error);
    if (error) {
        fprintf(stderr, "[boot] Could not prepare HDD directory '%s': %s\n",
                (root / usrdir).string().c_str(), error.message().c_str());
        return false;
    }
    const auto host_root = root.string();
    if (use_default) {
#ifdef _WIN32
        if (_putenv_s("PS3_HDD0_ROOT", host_root.c_str())) return false;
#else
        if (setenv("PS3_HDD0_ROOT", host_root.c_str(), 1)) return false;
#endif
        sys_fs_default_hdd0 = 1;
    }
    /* The library handler (including cellFsRename) has its own mount table. */
    cellfs_add_path_mapping("/dev_hdd0/", (host_root + "/").c_str());
    printf("[boot] HDD root: %s\n", host_root.c_str());
    return true;
}
