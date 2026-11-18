#include "../sceNpTrophy.c"
#include <assert.h>

uint8_t* vm_base;
uint32_t ppu_vm_size;
int g_resv_store_active;
uint32_t g_ww_lo, g_ww_hi;
void ppu_resv_break_store(uint64_t ea) { (void)ea; }
void ps3_ww_report_inline(uint32_t ea, uint64_t value, int width) { (void)ea; (void)value; (void)width; }
int spu_coh_is_reserved(uint32_t ea) { (void)ea; return 0; }
void spu_coh_notify_write(uint32_t ea) { (void)ea; }
void spu_lockline_lock(void) {}
void spu_lockline_unlock(void) {}

static void callback(u32 opd, u64 context, u64 status, u64 completed, u64 total,
                     u64 arg, u64 a, u64 b, u64 c)
{
    assert(opd == 0x300 && context == 1 && status == SCE_NP_TROPHY_STATUS_INSTALLED);
    assert(!completed && !total && !arg && !a && !b && !c);
}
ps3_guest_caller_fn g_ps3_guest_caller = callback;
s32 cellSysutilQueueGuestCallbackArgs(u32 opd, const u64 args[8])
{
    assert(opd == 0x300 && args[1] == SCE_NP_TROPHY_STATUS_PROCESSING_COMPLETE);
    return CELL_OK;
}

static void open_context(void)
{
    assert(sceNpTrophyInit(NULL, 0, 0, 0) == CELL_OK);
    assert(sceNpTrophyCreateContext((void*)0x200, (void*)0x100, NULL, 0) == CELL_OK);
    assert(sceNpTrophyCreateHandle((void*)0x204) == CELL_OK);
    assert(sceNpTrophyRegisterContext(1, 1, 0x300, 0, 0) == CELL_OK);
}

int main(void)
{
    vm_base = calloc(1, 65536);
    assert(vm_base);
    memcpy(vm_base + 0x100, "NPWR00001", 9);
    open_context();

    SceNpTrophyNotice notice;
    assert(!sceNpTrophyPopNotice(&notice)); /* Loading a legacy unlock must not replay a toast. */
    assert(sceNpTrophyGetGameInfo(1, 1, (void*)0x400, (void*)0x900) == CELL_OK);
    assert(vm_read32(0x400) == 3);
    assert(vm_read32(0x900) == 1);
    assert(sceNpTrophyUnlockTrophy(1, 1, 0, NULL) == SCE_NP_TROPHY_ERROR_PLATINUM_CANNOT_UNLOCK);
    assert(sceNpTrophyUnlockTrophy(1, 1, 1, NULL) == SCE_NP_TROPHY_ERROR_ALREADY_UNLOCKED);
    assert(sceNpTrophyUnlockTrophy(1, 1, 2, NULL) == SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT);

    assert(sceNpTrophyUnlockTrophy(1, 1, 3, (void*)0x220) == CELL_OK);
    assert(vm_read32(0x220) == 0);
    assert(sceNpTrophyPopNotice(&notice));
    assert(notice.grade == SCE_NP_TROPHY_GRADE_GOLD && !strcmp(notice.name, "A hidden trophy"));
    assert(sceNpTrophyPopNotice(&notice));
    assert(notice.grade == SCE_NP_TROPHY_GRADE_PLATINUM && !strcmp(notice.name, "Platinum"));
    assert(!sceNpTrophyPopNotice(&notice));
    assert(sceNpTrophyUnlockTrophy(1, 1, 3, NULL) == SCE_NP_TROPHY_ERROR_ALREADY_UNLOCKED);
    assert(sceNpTrophyGetGameProgress(1, 1, (void*)0x230) == CELL_OK);
    assert(vm_read32(0x230) == 100);
    assert(sceNpTrophyGetTrophyUnlockState(1, 1, (void*)0x240, (void*)0x260) == CELL_OK);
    assert(vm_read32(0x240) == 0xB && vm_read32(0x260) == 3);
    assert(sceNpTrophyTerm() == CELL_OK);

    open_context();
    assert(sceNpTrophyGetGameProgress(1, 1, (void*)0x230) == CELL_OK);
    assert(vm_read32(0x230) == 100);
    assert(!sceNpTrophyPopNotice(&notice));
    assert(sceNpTrophyTerm() == CELL_OK);
    free(vm_base);
    puts("Named trophy notices, platinum, and persistent progress passed");
    return 0;
}
