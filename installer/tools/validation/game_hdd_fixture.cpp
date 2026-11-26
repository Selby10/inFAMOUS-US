/* The guest ABI comes from the runtime headers. Only the guest memory and
 * diagnostic/decryption services outside this filesystem fixture are stubbed. */
#include "infamous-recomp/game_hdd.h"
#include "ps3recomp/runtime/ppu/ppu_fs.cpp"
#include <cassert>
#include <string>

static uint8_t memory[4096];
extern "C" {
uint8_t* vm_base = memory;
uint32_t ppu_vm_size = sizeof memory;
void vm_write32(uint64_t addr, uint32_t value) {
    memory[addr] = value >> 24; memory[addr + 1] = value >> 16;
    memory[addr + 2] = value >> 8; memory[addr + 3] = value;
}
void ppu_guest_caller(char*, size_t) {}
void ydkj_host_bt(const char*) {}
const char* edat_resolve(const char* path, char*, size_t) { return path; }
void ps3_hle_register_ctx(uint32_t, const char*, void (*)(ppu_context*)) {}
volatile long long g_hz[32];
int ps3_hz_is_main(void) { return 0; }
void vm_write64(uint64_t addr, uint64_t value) {
    vm_write32(addr, (uint32_t)(value >> 32));
    vm_write32(addr + 4, (uint32_t)value);
}
ps3_guest_caller_fn g_ps3_guest_caller;
unsigned ppu_prof_resolve_host(void*) { return 0; }
uint32_t g_ww_lo, g_ww_hi;
int g_resv_store_active;
void ps3_ww_report_inline(uint32_t, uint64_t, int) {}
int spu_coh_is_reserved(uint32_t) { return 0; }
void spu_lockline_lock(void) {}
void spu_lockline_unlock(void) {}
void spu_coh_notify_write(uint32_t) {}
void ppu_resv_break_store(uint64_t) {}
int32_t cellFsRename(const char*, const char*);
int cellfs_translate_path(const char*, char*, size_t);
int64_t sys_fs_open(ppu_context*);
int64_t sys_fs_close(ppu_context*);
}

static int raw_open(const std::string& name, unsigned flags)
{
    ppu_context ctx{};
    strcpy((char*)memory + 0x80, name.c_str());
    ctx.gpr[3] = 0x80;
    ctx.gpr[4] = flags;
    ctx.gpr[5] = 0x40;
    int result = (int)sys_fs_open(&ctx);
    if (result == 0) {
        ctx.gpr[3] = memory[0x43];
        assert(sys_fs_close(&ctx) == 0);
    }
    return result;
}

int main(int argc, char** argv)
{
    assert(argc == 3);
    ppu_vfs_root = argv[1];
    assert(configure_game_hdd(ppu_vfs_root, argv[2]));
    const std::string base = std::string("/dev_hdd0/game/") + argv[2] + "/USRDIR/";
    assert(raw_open(base + "ne.bv", 0x241) == 0);
    assert(raw_open(base + "ne.bv", 0) == 0);
    assert(raw_open(base + "ne.bv", 0xc1) == (int32_t)CELL_EEXIST);
    assert(raw_open(base + "legacy.bin", 0) ==
           (sys_fs_default_hdd0 ? 0 : (int32_t)CELL_ENOENT));
    char path[1100];
    host_path(path, sizeof path, (base + "legacy.bin").c_str());
    assert(!std::filesystem::exists(path));  /* disc source is not installed data */
    if (getenv("CHECK_SPLIT_CACHE")) {
        host_path(path, sizeof path, (base + "ppu.bin").c_str());
        assert(std::filesystem::exists(path));
        host_path(path, sizeof path, (base + "ppu.bin").c_str(), false);
        assert(!std::filesystem::exists(path));
        assert(cellfs_translate_path((base + "library.bin").c_str(), path, sizeof path) == 0);
        assert(std::filesystem::exists(path));
    }

    for (const char* image : {"ICON0.PNG", "PIC1.PNG"}) {
        std::string final = base + "../" + image;
        strcpy((char*)memory + 0x80, (final + ".tmp").c_str());
        strcpy((char*)memory + 0x200, final.c_str());
        ppu_context ctx{};
        ctx.gpr[3] = 0x80;
        ctx.gpr[4] = 0x41;   /* WRONLY|CREAT, without TRUNC */
        ctx.gpr[5] = 0x40;
        cellFsOpen(&ctx);
        assert(ctx.gpr[3] == 0);
        ctx.gpr[3] = memory[0x43];
        cellFsClose(&ctx);
        assert(cellFsRename((const char*)0x80, (const char*)0x200) == 0);
    }
}
