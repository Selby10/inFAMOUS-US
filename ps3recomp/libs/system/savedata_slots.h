/* inFamous uses slot 0 for autosave and slot 1 for manual saving on PS3.
 * Keep those paths compatible; additional manual slots use the same region prefix. */
#ifndef PS3RECOMP_SAVEDATA_SLOTS_H
#define PS3RECOMP_SAVEDATA_SLOTS_H
#include <string.h>
#include <stdio.h>
#define INFAMOUS_MANUAL_SLOTS 10
static inline int savedata_infamous_slot(const char* name)
{
    if (!name || (strncmp(name, "BCUS98119_INFAMOUS_", 19) &&
                  strncmp(name, "BCES00220_INFAMOUS_", 19))) return -1;
    const char* number = name + 19;
    if (number[0] >= '0' && number[0] <= '9' && !number[1]) return number[0] - '0';
    return !strcmp(number, "10") ? 10 : -1;
}
static inline void savedata_slot_name(char out[32], const char* original, int slot)
{
    snprintf(out, 32, "%.19s%d", original, slot);
}
/* Confirmation belongs to a particular slot; changing slots clears it in the UI. */
static inline int savedata_slot_accept(int is_save, int occupied, int index, int* pending)
{
    if (!is_save || !occupied || *pending == index) return 1;
    *pending = index;
    return 0;
}
#endif
