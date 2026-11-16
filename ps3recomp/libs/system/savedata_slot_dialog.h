/* Included by cellSaveData.c: native slot selector used by explicit ListSave/Load.
 * No disk writes occur here. Autosave APIs never enter this selector. */
#ifdef _WIN32
#include "../input/pad_host.h"
typedef struct {
    char name[32], label[320], detail[1600];
    int occupied;
} SaveSlotRow;
typedef struct {
    SaveSlotRow rows[INFAMOUS_MANUAL_SLOTS + 1];
    int count, initial, is_save, confirmed;
    unsigned previous;
    HWND list, details, action;
} SaveSlotDialog;

static void slot_wide(const char* text, WCHAR* out, int capacity)
{
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text, -1, out, capacity))
        MultiByteToWideChar(CP_ACP, 0, text, -1, out, capacity);
    out[capacity - 1] = 0;
}
static void slot_details(SaveSlotDialog* state)
{
    int index = (int)SendMessageW(state->list, LB_GETCURSEL, 0, 0);
    if (index < 0 || index >= state->count) return;
    WCHAR text[1700];
    slot_wide(state->rows[index].detail, text, 1700);
    SetWindowTextW(state->details, text);
    SetWindowTextW(state->action, state->is_save ? L"Save" : L"Load");
    state->confirmed = -1;
}
static INT_PTR CALLBACK slot_dialog_proc(HWND window, UINT message, WPARAM wp, LPARAM lp)
{
    SaveSlotDialog* state = (SaveSlotDialog*)GetWindowLongPtrW(window, DWLP_USER);
    if (message == WM_INITDIALOG) {
        state = (SaveSlotDialog*)lp;
        SetWindowLongPtrW(window, DWLP_USER, lp);
        SetWindowTextW(window, state->is_save ? L"inFamous - Save Game" : L"inFamous - Load Game");
        RECT r; GetClientRect(window, &r);
        int w = r.right, h = r.bottom;
        HWND intro = CreateWindowW(L"STATIC", state->is_save ?
            L"Choose a manual save slot. Autosave is kept separately.\nController: Up/Down, A/Cross to save, B/Circle to cancel." :
            L"Choose a save to load. Empty slots are hidden.\nController: Up/Down, A/Cross to load, B/Circle to cancel.",
            WS_CHILD | WS_VISIBLE, 12, 10, w - 24, 40, window, NULL, NULL, NULL);
        state->list = CreateWindowExW(WS_EX_CLIENTEDGE, L"LISTBOX", L"",
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_VSCROLL | LBS_NOTIFY | LBS_NOINTEGRALHEIGHT,
            12, 56, w - 24, h - 190, window, (HMENU)101, NULL, NULL);
        state->details = CreateWindowW(L"STATIC", L"", WS_CHILD | WS_VISIBLE,
            12, h - 124, w - 24, 72, window, NULL, NULL, NULL);
        state->action = CreateWindowW(L"BUTTON", state->is_save ? L"Save" : L"Load",
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
            w - 190, h - 40, 82, 28, window, (HMENU)IDOK, NULL, NULL);
        HWND cancel = CreateWindowW(L"BUTTON", L"Cancel", WS_CHILD | WS_VISIBLE | WS_TABSTOP,
            w - 98, h - 40, 82, 28, window, (HMENU)IDCANCEL, NULL, NULL);
        for (int i = 0; i < state->count; i++) {
            WCHAR text[400]; slot_wide(state->rows[i].label, text, 400);
            SendMessageW(state->list, LB_ADDSTRING, 0, (LPARAM)text);
        }
        HWND controls[] = {intro, state->list, state->details, state->action, cancel};
        for (unsigned i = 0; i < sizeof controls / sizeof controls[0]; i++)
            SendMessageW(controls[i], WM_SETFONT, (WPARAM)GetStockObject(DEFAULT_GUI_FONT), TRUE);
        SendMessageW(state->list, LB_SETCURSEL, state->initial, 0);
        slot_details(state);
        state->previous = ~0u; /* ignore controller buttons held when opening */
        SetTimer(window, 1, 30, NULL);
        SetFocus(state->list);
        return FALSE;
    }
    if (!state) return FALSE;
    if (message == WM_CLOSE) { EndDialog(window, 0); return TRUE; }
    if (message == WM_TIMER && s_slot_input && GetForegroundWindow() == window) {
        unsigned input = s_slot_input();
        unsigned down = input & ~state->previous;
        state->previous = input;
        if (down & PAD_UI_BACK) { EndDialog(window, 0); return TRUE; }
        if (down & (PAD_UI_UP | PAD_UI_DOWN)) {
            int index = (int)SendMessageW(state->list, LB_GETCURSEL, 0, 0);
            index += (down & PAD_UI_UP) ? -1 : 1;
            if (index < 0) index = state->count - 1;
            if (index >= state->count) index = 0;
            SendMessageW(state->list, LB_SETCURSEL, index, 0);
            slot_details(state);
        }
        if (down & PAD_UI_OK) SendMessageW(window, WM_COMMAND, IDOK, 0);
        return TRUE;
    }
    if (message == WM_COMMAND) {
        int id = LOWORD(wp), event = HIWORD(wp);
        if (id == IDCANCEL) { EndDialog(window, 0); return TRUE; }
        if (id == 101 && event == LBN_SELCHANGE) { slot_details(state); return TRUE; }
        if (id == IDOK || (id == 101 && event == LBN_DBLCLK)) {
            int index = (int)SendMessageW(state->list, LB_GETCURSEL, 0, 0);
            if (index < 0 || index >= state->count) return TRUE;
            if (!savedata_slot_accept(state->is_save, state->rows[index].occupied, index, &state->confirmed)) {
                SetWindowTextW(state->details, L"Replace the progress in this manual slot?\nChoose Overwrite again to confirm, or Cancel to keep the existing save.");
                SetWindowTextW(state->action, L"Overwrite");
                return TRUE;
            }
            EndDialog(window, index + 1);
            return TRUE;
        }
    }
    return FALSE;
}

static void savedata_collect_slots(const char* selected, int is_save, SaveSlotDialog* result)
{
    SaveSlotDialog state; memset(&state, 0, sizeof state);
    state.is_save = is_save;
    for (int slot = is_save ? 1 : 0; slot <= INFAMOUS_MANUAL_SLOTS; slot++) {
        char name[32], path[1024];
        savedata_slot_name(name, selected, slot);
        build_save_path(path, sizeof path, name);
        int exists = dir_has_save(path);
        if (!is_save && !exists) continue;
        SaveSlotRow* row = &state.rows[state.count];
        snprintf(row->name, sizeof row->name, "%s", name);
        row->occupied = dir_exists(path);
        CellSaveDataSystemFileParam param;
        read_param_sfo(path, &param);
        char stamp[64] = "", label[40]; HOST_STAT_T st;
        char sfo[1100]; snprintf(sfo, sizeof sfo, "%s/PARAM.SFO", path);
        if (exists && !HOST_STAT(sfo, &st)) {
            time_t seconds = (time_t)st.st_mtime; struct tm utc;
            if (!gmtime_s(&utc, &seconds)) strftime(stamp, sizeof stamp, "%Y-%m-%d %H:%M UTC", &utc);
        }
        if (slot) snprintf(label, sizeof label, "Manual slot %d", slot);
        else snprintf(label, sizeof label, "Autosave");
        const char* subtitle = exists ? (param.subTitle[0] ? param.subTitle : "Saved game") :
            row->occupied ? "Incomplete save" : "Empty";
        snprintf(row->label, sizeof row->label, "%s | %.128s%s%s", label, subtitle, stamp[0] ? " | " : "", stamp);
        snprintf(row->detail, sizeof row->detail, "%s\n%.128s\n%.1024s", row->label,
                 param.title[0] ? param.title : "inFamous", param.detail);
        if (!strcmp(name, selected)) state.initial = state.count;
        state.count++;
    }
    *result = state;
}

static int savedata_choose_infamous_slot(char selected[64], int is_save)
{
    SaveSlotDialog state; savedata_collect_slots(selected, is_save, &state);
    if (!state.count) return 0;
    union { DWORD alignment; BYTE bytes[64]; } buffer = {0};
    DLGTEMPLATE* dialog = (DLGTEMPLATE*)buffer.bytes;
    dialog->style = WS_POPUP | WS_CAPTION | WS_SYSMENU | DS_MODALFRAME | DS_CENTER;
    dialog->cx = 360; dialog->cy = 240;
    /* No menu, default dialog class, empty title; children are created at init. */
    HWND owner = GetForegroundWindow(); DWORD pid = 0;
    if (owner) GetWindowThreadProcessId(owner, &pid);
    if (pid != GetCurrentProcessId()) owner = NULL;
    g_savedata_slot_dialog_active = 1;
    INT_PTR result = DialogBoxIndirectParamW(GetModuleHandleW(NULL), dialog, owner, slot_dialog_proc, (LPARAM)&state);
    g_savedata_slot_dialog_active = 0;
    if (result <= 0 || result > state.count) return 0; /* fail closed if UI creation fails */
    snprintf(selected, 64, "%s", state.rows[result - 1].name);
    return 1;
}
#endif
