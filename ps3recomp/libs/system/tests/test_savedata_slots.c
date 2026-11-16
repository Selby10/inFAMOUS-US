/* Actual guest callbacks + file IO, using an isolated working directory. */
#include "../cellSaveData.c"
#include <assert.h>
uint8_t* vm_base;
uint32_t ppu_vm_size;
int g_resv_store_active;
uint32_t g_ww_lo, g_ww_hi;
void ppu_resv_break_store(uint64_t a) { (void)a; }
void ps3_ww_report_inline(uint32_t a, uint64_t v, int w) { (void)a; (void)v; (void)w; }
int spu_coh_is_reserved(uint32_t a) { (void)a; return 0; }
void spu_coh_notify_write(uint32_t a) { (void)a; }
void spu_lockline_lock(void) {}
void spu_lockline_unlock(void) {}
void ps3_hle_register_ctx(uint32_t nid, const char* name, void (*fn)(ppu_context*))
{ (void)nid; (void)name; (void)fn; }
ps3_guest_caller_fn g_ps3_guest_caller;
static int writing, phase;
static unsigned char value;
static const char* chosen;
static void callback(u32 opd, u64 cb, u64 get, u64 set, u64 a, u64 b, u64 c, u64 d, u64 e)
{
    (void)a; (void)b; (void)c; (void)d; (void)e;
    if (opd == 0x800) {
        strcpy((char*)vm_base + 0x380, chosen);
        if (writing) { vm_write32(set + 16, 0x500); vm_write32(0x504, 0x380); }
        else { vm_write32(set + 8, 1); vm_write32(set + 12, 0x380); }
    } else if (opd == 0x900) {
        assert(!strcmp((char*)vm_base + get + 32, chosen));
        if (writing) { strcpy((char*)vm_base + 0x600, "inFamous"); strcpy((char*)vm_base + 0x680, "Synthetic checkpoint"); vm_write32(set, 0x600); }
        phase = 0;
    } else {
        assert(opd == 0xA00);
        if (phase++) { vm_write32(cb, CELL_SAVEDATA_CBRESULT_OK_LAST); return; }
        vm_write32(set, writing ? CELL_SAVEDATA_FILEOP_WRITE : CELL_SAVEDATA_FILEOP_READ);
        vm_write32(set + 8, CELL_SAVEDATA_FILETYPE_NORMALFILE);
        strcpy((char*)vm_base + 0x700, "DATA.BIN");
        vm_write32(set + 28, 0x700);
        vm_write32(set + 36, 1); vm_write32(set + 40, 16); vm_write32(set + 44, 0x800);
        vm_base[0x800] = writing ? value : 0;
    }
}
static void operation(const char* name, int save, unsigned char byte)
{
    chosen = name; writing = save; value = byte;
    s32 result;
    if (savedata_infamous_slot(name) == 0) {
#ifdef _WIN32
        _putenv_s("SAVEDATA_NATIVE_UI", "1"); /* automatic operations must stay noninteractive */
#endif
        result = save ? cellSaveDataAutoSave2(0, name, 0, (void*)0x200,
            (void*)0x900, (void*)0xA00, 0, NULL) :
            cellSaveDataAutoLoad2(0, name, 0, (void*)0x200,
            (void*)0x900, (void*)0xA00, 0, NULL);
#ifdef _WIN32
        _putenv_s("SAVEDATA_NATIVE_UI", "0");
#endif
    } else result = save ? cellSaveDataListSave2(0, (void*)0x100, (void*)0x200,
        (void*)0x800, (void*)0x900, (void*)0xA00, 0, NULL) :
        cellSaveDataListLoad2(0, (void*)0x100, (void*)0x200,
        (void*)0x800, (void*)0x900, (void*)0xA00, 0, NULL);
    assert(result == CELL_OK);
    if (!save) assert(vm_base[0x800] == byte);
}
int main(void)
{
#if defined(_WIN32) && defined(SAVEDATA_SLOT_UI_FIXTURE)
    char location[MAX_PATH]; GetModuleFileNameA(NULL, location, MAX_PATH);
    char* slash = strrchr(location, '\\'); assert(slash); *slash = 0;
    assert(SetCurrentDirectoryA(location));
#endif
    assert(savedata_infamous_slot("BCUS98119_INFAMOUS_0") == 0);
    assert(savedata_infamous_slot("BCES00220_INFAMOUS_10") == 10);
    assert(savedata_infamous_slot("BCUS98119_INFAMOUS_11") == -1);
    assert(savedata_infamous_slot("BCUS98119_INFAMOUS_1/../../x") == -1);
    assert(savedata_infamous_slot("OTHER") == -1);
    int confirmation = -1;
    assert(!savedata_slot_accept(1, 1, 0, &confirmation));
    assert(savedata_slot_accept(1, 1, 0, &confirmation));
    assert(!savedata_slot_accept(1, 1, 2, &confirmation)); /* approval doesn't transfer */
    assert(savedata_slot_accept(1, 0, 1, &confirmation)); /* empty slot needs no overwrite */
    assert(savedata_slot_accept(0, 1, 2, &confirmation)); /* load never asks to overwrite */
    vm_base = calloc(1, 0x200000); assert(vm_base);
    assert(cellSaveData_set_scratch_region(0x100000, 0x20000) == CELL_OK);
    strcpy(s_save_root, "saves");
#ifdef _WIN32
    _putenv_s("SAVEDATA_ROOT", "saves"); _putenv_s("SAVEDATA_NATIVE_UI", "0");
#else
    setenv("SAVEDATA_ROOT", "saves", 1); setenv("SAVEDATA_NATIVE_UI", "0", 1);
#endif
    strcpy((char*)vm_base + 0x300, "BCUS98119_INFAMOUS_");
    vm_write32(0x108, 0x300); vm_write32(0x200, 64); vm_write32(0x204, 64);
    vm_write32(0x220, 4096); vm_write32(0x224, 0x4000);
    g_ps3_guest_caller = callback;
    operation("BCUS98119_INFAMOUS_1", 1, 'A');
    operation("BCUS98119_INFAMOUS_3", 1, 'B');
    operation("BCUS98119_INFAMOUS_0", 1, 'C');
    operation("BCUS98119_INFAMOUS_10", 1, 'D');
    operation("BCUS98119_INFAMOUS_1", 0, 'A');
    operation("BCUS98119_INFAMOUS_3", 0, 'B');
    operation("BCUS98119_INFAMOUS_0", 0, 'C');
    operation("BCUS98119_INFAMOUS_10", 0, 'D');
    operation("BCUS98119_INFAMOUS_3", 1, 'E');
    operation("BCUS98119_INFAMOUS_1", 0, 'A');
    operation("BCUS98119_INFAMOUS_3", 0, 'E');
    operation("BCUS98119_INFAMOUS_0", 0, 'C');
#ifdef _WIN32
    SaveSlotDialog list;
    savedata_collect_slots("BCUS98119_INFAMOUS_1", 1, &list);
    assert(list.count == 10 && list.rows[0].occupied && !list.rows[1].occupied);
    assert(!strcmp(list.rows[9].name, "BCUS98119_INFAMOUS_10"));
    savedata_collect_slots("BCUS98119_INFAMOUS_3", 0, &list);
    assert(list.count == 4 && list.initial == 2);
    assert(!strcmp(list.rows[0].name, "BCUS98119_INFAMOUS_0"));
    savedata_collect_slots("BCES00220_INFAMOUS_1", 0, &list); assert(!list.count);
#ifdef SAVEDATA_SLOT_UI_FIXTURE
    char selected[64] = "BCUS98119_INFAMOUS_1";
    int accepted = savedata_choose_infamous_slot(selected, 1);
    FILE* report = fopen("ui-result.txt", "w"); assert(report);
    fprintf(report, "Save accepted=%d selected=%s\n", accepted, selected); fflush(report);
    strcpy(selected, "BCUS98119_INFAMOUS_1");
    accepted = savedata_choose_infamous_slot(selected, 0);
    fprintf(report, "Load accepted=%d selected=%s\n", accepted, selected);
    fclose(report);
#endif
#endif
    free(vm_base);
    puts("Multi-slot callback/file IO and autosave isolation passed");
}
