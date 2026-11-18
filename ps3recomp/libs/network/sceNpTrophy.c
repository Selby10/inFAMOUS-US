/*
 * ps3recomp - sceNpTrophy HLE implementation
 *
 * Trophy management with persistent storage.  Unlocked trophies are saved
 * to a JSON file at gamedata/trophies/{commId}.json.
 *
 * JSON format (hand-written, no external dependency):
 * { "trophies": [ { "id": N, "timestamp": T }, ... ] }
 */

#include "sceNpTrophy.h"
#include "../system/cellSysutil.h"
#include "ps3emu/guest_call.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdint.h>
#include "../../runtime/ppu/ppu_memory.h"   /* vm_base (guest mem) */
#include "ps3emu/endian.h"                   /* ps3_bswap64 -- guest is big-endian */
#include "../guest_struct.h"   /* GUEST_EA, guest_struct_load/store */
/* HLE args arrive as guest effective addresses; translate before deref. */
#define GUEST_PTR(p, T) ((T)((p) ? (void*)(vm_base + (uint32_t)(uintptr_t)(p)) : (void*)0))

#ifdef _WIN32
#include <direct.h>
#include <io.h>
#include <windows.h>
#define mkdir_p(path) _mkdir(path)
#define PATH_SEP '\\'
#else
#include <sys/stat.h>
#include <unistd.h>
#include <pthread.h>
#define mkdir_p(path) mkdir(path, 0755)
#define PATH_SEP '/'
#endif

/* ---------------------------------------------------------------------------
 * Internal state
 * -----------------------------------------------------------------------*/

static int s_trophy_initialized = 0;
static char s_storage_path[512] = "gamedata/trophies";

/* Per-context data */
typedef struct {
    int                      in_use;
    int                      registered;
    SceNpCommunicationId     commId;
    u8                       unlocked[SCE_NP_TROPHY_MAX_NUM_TROPHIES];
    u64                      unlock_time[SCE_NP_TROPHY_MAX_NUM_TROPHIES];
    u32                      total_trophies;
    int                      has_catalog;
} TrophyContext;

typedef struct {
    int in_use;
} TrophyHandle;

/* ID zero is the firmware invalid sentinel; retain all four usable slots. */
static TrophyContext s_contexts[SCE_NP_TROPHY_MAX_CONTEXTS + 1];
static TrophyHandle  s_handles[SCE_NP_TROPHY_MAX_HANDLES + 1];

typedef struct {
    int grade, platinum, hidden;
    char name[SCE_NP_TROPHY_NAME_MAX_SIZE];
    char description[SCE_NP_TROPHY_DESC_MAX_SIZE];
} TrophyMetadata;

static TrophyMetadata s_catalog[SCE_NP_TROPHY_MAX_NUM_TROPHIES];
static char s_catalog_id[SCE_NP_COMMUNICATION_ID_MAX_LENGTH + 1];
static unsigned s_catalog_number, s_catalog_count;

#ifdef _WIN32
static SRWLOCK s_trophy_lock = SRWLOCK_INIT;
#define trophy_lock() AcquireSRWLockExclusive(&s_trophy_lock)
#define trophy_try_lock() TryAcquireSRWLockExclusive(&s_trophy_lock)
#define trophy_unlock() ReleaseSRWLockExclusive(&s_trophy_lock)
#else
static pthread_mutex_t s_trophy_lock = PTHREAD_MUTEX_INITIALIZER;
#define trophy_lock() pthread_mutex_lock(&s_trophy_lock)
#define trophy_try_lock() (pthread_mutex_trylock(&s_trophy_lock) == 0)
#define trophy_unlock() pthread_mutex_unlock(&s_trophy_lock)
#endif

#define TROPHY_QUEUE_SIZE SCE_NP_TROPHY_MAX_NUM_TROPHIES
static SceNpTrophyNotice s_notices[TROPHY_QUEUE_SIZE];
static unsigned s_notice_read, s_notice_count;

int sceNpTrophyPopNotice(SceNpTrophyNotice* out)
{
    int pending;
    if (!trophy_try_lock()) return 0;
    if ((pending = s_notice_count != 0)) {
        *out = s_notices[s_notice_read];
        s_notice_read = (s_notice_read + 1) % TROPHY_QUEUE_SIZE;
        s_notice_count--;
    }
    trophy_unlock();
    return pending;
}

static int trophy_grade(char letter)
{
    switch (letter) {
    case 'P': return SCE_NP_TROPHY_GRADE_PLATINUM;
    case 'G': return SCE_NP_TROPHY_GRADE_GOLD;
    case 'S': return SCE_NP_TROPHY_GRADE_SILVER;
    case 'B': return SCE_NP_TROPHY_GRADE_BRONZE;
    default: return SCE_NP_TROPHY_GRADE_UNKNOWN;
    }
}

static void trophy_load_catalog(void)
{
    char path[1024], line[1024];
    memset(s_catalog, 0, sizeof(s_catalog));
    s_catalog_count = 0;
    snprintf(path, sizeof(path), "%s/catalog.tsv", s_storage_path);
    FILE* f = fopen(path, "r");
    if (!f) return;
    if (fgets(line, sizeof(line), f) &&
        sscanf(line, "TROPHIES1\t%9[A-Za-z0-9]\t%u", s_catalog_id, &s_catalog_number) == 2) {
        while (fgets(line, sizeof(line), f)) {
            char grade;
            unsigned id;
            int linked, hidden, offset = 0;
            if (sscanf(line, "%u\t%c\t%d\t%d\t%n", &id, &grade, &linked, &hidden, &offset) != 4 ||
                offset <= 0 || id >= SCE_NP_TROPHY_MAX_NUM_TROPHIES ||
                linked < -1 || linked >= SCE_NP_TROPHY_MAX_NUM_TROPHIES ||
                !trophy_grade(grade) || s_catalog[id].grade) {
                s_catalog_count = 0;
                break;
            }
            char* name = line + offset;
            name[strcspn(name, "\r\n")] = '\0';
            char* detail = strchr(name, '\t');
            if (detail) *detail++ = '\0';
            TrophyMetadata* info = &s_catalog[id];
            info->grade = trophy_grade(grade);
            info->platinum = linked;
            info->hidden = hidden != 0;
            snprintf(info->name, sizeof(info->name), "%s", name);
            if (detail) snprintf(info->description, sizeof(info->description), "%s", detail);
            s_catalog_count++;
        }
    }
    fclose(f);
}

static int trophy_valid(const TrophyContext* ctx, s32 id)
{
    return id >= 0 && id < SCE_NP_TROPHY_MAX_NUM_TROPHIES &&
           (!ctx->has_catalog || s_catalog[id].grade != SCE_NP_TROPHY_GRADE_UNKNOWN);
}

/* Called with the trophy lock held, only for a newly granted trophy. */
static void trophy_award(TrophyContext* ctx, s32 id)
{
    ctx->unlocked[id] = 1;
    ctx->unlock_time[id] = (u64)time(NULL);
    if (s_notice_count < TROPHY_QUEUE_SIZE) {
        SceNpTrophyNotice* notice = &s_notices[(s_notice_read + s_notice_count++) % TROPHY_QUEUE_SIZE];
        notice->grade = ctx->has_catalog ? s_catalog[id].grade : SCE_NP_TROPHY_GRADE_UNKNOWN;
        if (ctx->has_catalog && s_catalog[id].name[0])
            snprintf(notice->name, sizeof(notice->name), "%s", s_catalog[id].name);
        else
            snprintf(notice->name, sizeof(notice->name), "Trophy %d", id);
        printf("[sceNpTrophy] Unlocked %d (%s)\n", id, notice->name);
    }
}

/* ---------------------------------------------------------------------------
 * Persistent storage helpers
 * -----------------------------------------------------------------------*/

static void trophy_ensure_dir(void)
{
    char tmp[512];
    char* p;

    strncpy(tmp, s_storage_path, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';

    /* Create directories recursively */
    for (p = tmp + 1; *p; p++) {
        if (*p == '/' || *p == '\\') {
            *p = '\0';
            mkdir_p(tmp);
            *p = PATH_SEP;
        }
    }
    mkdir_p(tmp);
}

static void trophy_get_filepath(const SceNpCommunicationId* commId,
                                 char* buf, size_t bufsize)
{
    snprintf(buf, bufsize, "%s/%.*s.json", s_storage_path, SCE_NP_COMMUNICATION_ID_MAX_LENGTH, commId->data);
}

static void trophy_save(TrophyContext* ctx)
{
    char filepath[1024];
    char pending[1040];
    FILE* fp;
    int first = 1;

    trophy_ensure_dir();
    trophy_get_filepath(&ctx->commId, filepath, sizeof(filepath));

    snprintf(pending, sizeof(pending), "%s.tmp", filepath);
    fp = fopen(pending, "w");
    if (!fp) {
        printf("[sceNpTrophy] WARNING: Could not save trophies to %s\n",
               filepath);
        return;
    }

    fprintf(fp, "{\n  \"trophies\": [");
    for (u32 i = 0; i < SCE_NP_TROPHY_MAX_NUM_TROPHIES; i++) {
        if (trophy_valid(ctx, (s32)i) && ctx->unlocked[i]) {
            if (!first) fprintf(fp, ",");
            fprintf(fp, "\n    { \"id\": %u, \"timestamp\": %llu }",
                    i, (unsigned long long)ctx->unlock_time[i]);
            first = 0;
        }
    }
    fprintf(fp, "\n  ]\n}\n");
    int failed = ferror(fp);
    if (fclose(fp) != 0) failed = 1;
#ifdef _WIN32
    if (!failed && !MoveFileExA(pending, filepath, MOVEFILE_REPLACE_EXISTING)) failed = 1;
#else
    if (!failed && rename(pending, filepath) != 0) failed = 1;
#endif
    if (failed) {
        remove(pending);
        printf("[sceNpTrophy] WARNING: Could not write trophy history to %s\n", filepath);
    }
}

static void trophy_load(TrophyContext* ctx)
{
    char filepath[1024];
    FILE* fp;
    char line[256];

    trophy_get_filepath(&ctx->commId, filepath, sizeof(filepath));

    fp = fopen(filepath, "r");
    if (!fp)
        return; /* no saved data, all locked */

    /*
     * Simple JSON parser -- look for "id": N and "timestamp": T patterns.
     * This avoids any external JSON library dependency.
     */
    while (fgets(line, sizeof(line), fp)) {
        const char* id_pos = strstr(line, "\"id\":");
        if (id_pos) {
            u32 tid = 0;
            unsigned long long ts = 0;
            const char* ts_pos;

            if (sscanf(id_pos, "\"id\": %u", &tid) == 1 &&
                tid < SCE_NP_TROPHY_MAX_NUM_TROPHIES && trophy_valid(ctx, (s32)tid)) {
                ts_pos = strstr(line, "\"timestamp\":");
                if (ts_pos)
                    sscanf(ts_pos, "\"timestamp\": %llu",
                           &ts);
                ctx->unlocked[tid] = 1;
                ctx->unlock_time[tid] = ts;
            }
        }
    }
    fclose(fp);

    printf("[sceNpTrophy] Loaded trophy data from %s\n", filepath);
}

static u32 trophy_count_unlocked(TrophyContext* ctx)
{
    u32 count = 0;
    for (u32 i = 0; i < SCE_NP_TROPHY_MAX_NUM_TROPHIES; i++)
        if (trophy_valid(ctx, (s32)i) && ctx->unlocked[i]) count++;
    return count;
}

/* ---------------------------------------------------------------------------
 * API implementations
 * -----------------------------------------------------------------------*/

void sceNpTrophySetStoragePath(const char* path)
{
    path = GUEST_PTR(path, const char*);
    if (path) {
        strncpy(s_storage_path, path, sizeof(s_storage_path) - 1);
        s_storage_path[sizeof(s_storage_path) - 1] = '\0';
    }
}

s32 sceNpTrophyInit(void* poolPtr, u32 poolSize, u32 containerId, u64 options)
{
    (void)poolPtr; (void)poolSize; (void)containerId; (void)options;

    printf("[sceNpTrophy] Init()\n");

    if (s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_ALREADY_INITIALIZED;

    memset(s_contexts, 0, sizeof(s_contexts));
    memset(s_handles, 0, sizeof(s_handles));
    trophy_load_catalog();
    s_trophy_initialized = 1;
    return CELL_OK;
}

s32 sceNpTrophyTerm(void)
{
    printf("[sceNpTrophy] Term()\n");

    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    trophy_lock();
    /* Save all registered contexts */
    for (int i = 1; i <= SCE_NP_TROPHY_MAX_CONTEXTS; i++) {
        if (s_contexts[i].in_use && s_contexts[i].registered)
            trophy_save(&s_contexts[i]);
    }

    memset(s_contexts, 0, sizeof(s_contexts));
    memset(s_handles, 0, sizeof(s_handles));
    s_trophy_initialized = 0;
    trophy_unlock();
    return CELL_OK;
}

s32 sceNpTrophyCreateContext(SceNpTrophyContext* context,
                             const SceNpCommunicationId* commId,
                             const SceNpCommunicationSignature* commSign,
                             u64 options)
{
    (void)commSign; (void)options;

    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (!context || !commId)
        return SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT;
    const SceNpCommunicationId* commId_h = GUEST_PTR(commId, const SceNpCommunicationId*);

    for (s32 i = 1; i <= SCE_NP_TROPHY_MAX_CONTEXTS; i++) {
        if (!s_contexts[i].in_use) {
            memset(&s_contexts[i], 0, sizeof(TrophyContext));
            s_contexts[i].in_use = 1;
            s_contexts[i].commId = *commId_h;
            s_contexts[i].has_catalog = s_catalog_count && commId_h->num == s_catalog_number &&
                memcmp(commId_h->data, s_catalog_id, SCE_NP_COMMUNICATION_ID_MAX_LENGTH) == 0;
            s_contexts[i].total_trophies = s_contexts[i].has_catalog ?
                s_catalog_count : SCE_NP_TROPHY_MAX_NUM_TROPHIES;
            vm_write32(GUEST_EA(context), (u32)i);
            printf("[sceNpTrophy] CreateContext(commId=\"%.9s\") -> ctx=%d (%u trophies)\n",
                   commId_h->data, i, s_contexts[i].total_trophies);
            return CELL_OK;
        }
    }

    return SCE_NP_TROPHY_ERROR_OUT_OF_MEMORY;
}

s32 sceNpTrophyDestroyContext(SceNpTrophyContext context)
{
    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (context <= 0 || context > SCE_NP_TROPHY_MAX_CONTEXTS ||
        !s_contexts[context].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_CONTEXT;

    trophy_lock();
    if (s_contexts[context].registered)
        trophy_save(&s_contexts[context]);

    s_contexts[context].in_use = 0;
    trophy_unlock();
    printf("[sceNpTrophy] DestroyContext(ctx=%d)\n", context);
    return CELL_OK;
}

s32 sceNpTrophyCreateHandle(SceNpTrophyHandle* handle)
{
    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (!handle)
        return SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT;

    for (s32 i = 1; i <= SCE_NP_TROPHY_MAX_HANDLES; i++) {
        if (!s_handles[i].in_use) {
            s_handles[i].in_use = 1;
            vm_write32(GUEST_EA(handle), (u32)i);
            printf("[sceNpTrophy] CreateHandle() -> handle=%d\n", i);
            return CELL_OK;
        }
    }

    return SCE_NP_TROPHY_ERROR_OUT_OF_MEMORY;
}

s32 sceNpTrophyDestroyHandle(SceNpTrophyHandle handle)
{
    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (handle <= 0 || handle > SCE_NP_TROPHY_MAX_HANDLES ||
        !s_handles[handle].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_HANDLE;

    s_handles[handle].in_use = 0;
    printf("[sceNpTrophy] DestroyHandle(handle=%d)\n", handle);
    return CELL_OK;
}

s32 sceNpTrophyRegisterContext(SceNpTrophyContext context,
                               SceNpTrophyHandle handle,
                               u32 statusCb,
                               u32 arg,
                               u64 options)
{
    (void)options;

    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (context <= 0 || context > SCE_NP_TROPHY_MAX_CONTEXTS ||
        !s_contexts[context].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_CONTEXT;

    if (handle <= 0 || handle > SCE_NP_TROPHY_MAX_HANDLES ||
        !s_handles[handle].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_HANDLE;

    if (s_contexts[context].registered)
        return SCE_NP_TROPHY_ERROR_CONTEXT_ALREADY_REG;

    if (!statusCb) return SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT;
    if (!g_ps3_guest_caller) return SCE_NP_TROPHY_ERROR_UNKNOWN;

    /* Load any previously saved trophy data */
    trophy_lock();
    trophy_load(&s_contexts[context]);
    s_contexts[context].registered = 1;
    trophy_unlock();

    printf("[sceNpTrophy] RegisterContext(ctx=%d, handle=%d, statusCb=0x%08X, "
           "arg=0x%08X) -> loaded saved data\n", context, handle, statusCb, arg);

    /* Use the runner's OPD bridge, not the optional code/TOC scaffold entry
     * point. Registration status is synchronous; its terminal notification is
     * delivered through sysutil polling, after the HLE storage load completes.
     * Intermediate firmware installation progress is not simulated here. */
    g_ps3_guest_caller(statusCb, (u32)context, SCE_NP_TROPHY_STATUS_INSTALLED,
                       0, 0, arg, 0, 0, 0);
    if (!s_trophy_initialized) return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;
    if (!s_contexts[context].in_use) return SCE_NP_TROPHY_ERROR_INVALID_CONTEXT;
    if (!s_handles[handle].in_use) return SCE_NP_TROPHY_ERROR_INVALID_HANDLE;
    const u64 completion[8] = {(u32)context, SCE_NP_TROPHY_STATUS_PROCESSING_COMPLETE,
                               0, 0, arg, 0, 0, 0};
    if (cellSysutilQueueGuestCallbackArgs(statusCb, completion) != CELL_OK) {
        s_contexts[context].registered = 0;
        return SCE_NP_TROPHY_ERROR_OUT_OF_MEMORY;
    }

    return CELL_OK;
}

s32 sceNpTrophyGetRequiredDiskSpace(SceNpTrophyContext context,
                                    SceNpTrophyHandle handle,
                                    u64* reqSpace, u64 options)
{
    (void)handle; (void)options;

    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (context <= 0 || context > SCE_NP_TROPHY_MAX_CONTEXTS ||
        !s_contexts[context].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_CONTEXT;

    if (!reqSpace)
        return SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT;
    reqSpace = GUEST_PTR(reqSpace, u64*);

    /* Typical trophy pack size. The guest is big-endian and reads this u64
     * back in BE order, so the value MUST be byte-swapped on write -- otherwise
     * a host-LE 0x100000 is read as 0x0010000000000000 (2^44). LBP feeds this
     * "required disk space" straight into its save-data free-space accounting
     * (sub_379EC0: errB = budget - usedKB - reqSpaceKB); a garbage 2^44 makes
     * errB underflow negative -> cellGame GameData check returns ERROR-B ->
     * boot bails. (Same LE-write class as the cellUserInfo fix.) */
    *reqSpace = ps3_bswap64((u64)(1024 * 1024)); /* 1 MB, big-endian */
    printf("[sceNpTrophy] GetRequiredDiskSpace(ctx=%d) -> 1MB\n", context);
    return CELL_OK;
}

s32 sceNpTrophyGetGameInfo(SceNpTrophyContext context,
                           SceNpTrophyHandle handle,
                           SceNpTrophyGameDetails* details,
                           SceNpTrophyGameData* data)
{
    (void)handle;

    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (context <= 0 || context > SCE_NP_TROPHY_MAX_CONTEXTS ||
        !s_contexts[context].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_CONTEXT;

    if (!s_contexts[context].registered)
        return SCE_NP_TROPHY_ERROR_CONTEXT_NOT_REGISTERED;
    details = GUEST_PTR(details, SceNpTrophyGameDetails*);
    data = GUEST_PTR(data, SceNpTrophyGameData*);

    TrophyContext* ctx = &s_contexts[context];
    u32 totals[5] = {0}, unlocked[5] = {0};
    trophy_lock();
    for (s32 i = 0; i < SCE_NP_TROPHY_MAX_NUM_TROPHIES; i++) {
        if (!trophy_valid(ctx, i)) continue;
        int grade = ctx->has_catalog ? s_catalog[i].grade : SCE_NP_TROPHY_GRADE_BRONZE;
        totals[grade]++;
        if (ctx->unlocked[i]) unlocked[grade]++;
    }
    u32 total_unlocked = trophy_count_unlocked(ctx);
    trophy_unlock();
    if (details) {
        memset(details, 0, sizeof(*details));
        details->numTrophies = ps3_bswap32(ctx->total_trophies);
        details->numPlatinum = ps3_bswap32(totals[SCE_NP_TROPHY_GRADE_PLATINUM]);
        details->numGold = ps3_bswap32(totals[SCE_NP_TROPHY_GRADE_GOLD]);
        details->numSilver = ps3_bswap32(totals[SCE_NP_TROPHY_GRADE_SILVER]);
        details->numBronze = ps3_bswap32(totals[SCE_NP_TROPHY_GRADE_BRONZE]);
        snprintf(details->title, sizeof(details->title), "%s", ctx->has_catalog ? "inFamous" : "PS3 Game");
    }
    if (data) {
        memset(data, 0, sizeof(*data));
        data->unlockedTrophies = ps3_bswap32(total_unlocked);
        data->unlockedPlatinum = ps3_bswap32(unlocked[SCE_NP_TROPHY_GRADE_PLATINUM]);
        data->unlockedGold = ps3_bswap32(unlocked[SCE_NP_TROPHY_GRADE_GOLD]);
        data->unlockedSilver = ps3_bswap32(unlocked[SCE_NP_TROPHY_GRADE_SILVER]);
        data->unlockedBronze = ps3_bswap32(unlocked[SCE_NP_TROPHY_GRADE_BRONZE]);
    }

    printf("[sceNpTrophy] GetGameInfo(ctx=%d)\n", context);
    return CELL_OK;
}

s32 sceNpTrophyGetTrophyInfo(SceNpTrophyContext context,
                             SceNpTrophyHandle handle,
                             SceNpTrophyId trophyId,
                             SceNpTrophyDetails* details,
                             SceNpTrophyData* data)
{
    (void)handle;

    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (context <= 0 || context > SCE_NP_TROPHY_MAX_CONTEXTS ||
        !s_contexts[context].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_CONTEXT;

    if (!s_contexts[context].registered)
        return SCE_NP_TROPHY_ERROR_CONTEXT_NOT_REGISTERED;

    TrophyContext* ctx = &s_contexts[context];
    if (!trophy_valid(ctx, trophyId))
        return SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT;
    details = GUEST_PTR(details, SceNpTrophyDetails*);
    data = GUEST_PTR(data, SceNpTrophyData*);

    if (details) {
        memset(details, 0, sizeof(*details));
        details->trophyId = ps3_bswap32((u32)trophyId);
        if (ctx->has_catalog) {
            const TrophyMetadata* info = &s_catalog[trophyId];
            details->trophyGrade = ps3_bswap32((u32)info->grade);
            snprintf(details->name, sizeof(details->name), "%s", info->name);
            snprintf(details->description, sizeof(details->description), "%s", info->description);
            details->hidden = (u8)info->hidden;
        } else {
            details->trophyGrade = ps3_bswap32(SCE_NP_TROPHY_GRADE_BRONZE);
            snprintf(details->name, sizeof(details->name), "Trophy %d", trophyId);
        }
    }

    if (data) {
        memset(data, 0, sizeof(*data));
        data->trophyId = ps3_bswap32((u32)trophyId);
        trophy_lock();
        data->unlocked = ctx->unlocked[trophyId];
        data->timestamp = ps3_bswap64(ctx->unlock_time[trophyId]);
        trophy_unlock();
    }

    return CELL_OK;
}

s32 sceNpTrophyUnlockTrophy(SceNpTrophyContext context,
                            SceNpTrophyHandle handle,
                            SceNpTrophyId trophyId,
                            SceNpTrophyId* platinumId)
{
    (void)handle;

    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (context <= 0 || context > SCE_NP_TROPHY_MAX_CONTEXTS ||
        !s_contexts[context].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_CONTEXT;

    if (!s_contexts[context].registered)
        return SCE_NP_TROPHY_ERROR_CONTEXT_NOT_REGISTERED;

    TrophyContext* ctx = &s_contexts[context];
    if (!trophy_valid(ctx, trophyId))
        return SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT;
    if (ctx->has_catalog && s_catalog[trophyId].grade == SCE_NP_TROPHY_GRADE_PLATINUM)
        return SCE_NP_TROPHY_ERROR_PLATINUM_CANNOT_UNLOCK;

    trophy_lock();
    if (ctx->unlocked[trophyId]) {
        trophy_unlock();
        return SCE_NP_TROPHY_ERROR_ALREADY_UNLOCKED;
    }
    trophy_award(ctx, trophyId);
    s32 awarded = SCE_NP_TROPHY_INVALID_TROPHY_ID;
    s32 linked = ctx->has_catalog ? s_catalog[trophyId].platinum : -1;
    if (linked >= 0 && s_catalog[linked].grade == SCE_NP_TROPHY_GRADE_PLATINUM &&
        !ctx->unlocked[linked]) {
        int complete = 1;
        for (s32 i = 0; i < SCE_NP_TROPHY_MAX_NUM_TROPHIES; i++) {
            if (trophy_valid(ctx, i) && s_catalog[i].platinum == linked && !ctx->unlocked[i]) {
                complete = 0;
                break;
            }
        }
        if (complete) {
            trophy_award(ctx, linked);
            awarded = linked;
        }
    }
    trophy_save(ctx);
    trophy_unlock();

    if (platinumId)
        vm_write32(GUEST_EA(platinumId), (u32)awarded);
    return CELL_OK;
}

s32 sceNpTrophyGetTrophyUnlockState(SceNpTrophyContext context,
                                    SceNpTrophyHandle handle,
                                    SceNpTrophyFlagArray* flags,
                                    u32* count)
{
    (void)handle;

    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (context <= 0 || context > SCE_NP_TROPHY_MAX_CONTEXTS ||
        !s_contexts[context].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_CONTEXT;

    if (!s_contexts[context].registered)
        return SCE_NP_TROPHY_ERROR_CONTEXT_NOT_REGISTERED;

    if (!flags || !count)
        return SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT;

    u32 flags_ea = (u32)(uintptr_t)flags;
    for (u32 o = 0; o < sizeof(SceNpTrophyFlagArray); o += 4)
        vm_write32(flags_ea + o, 0);

    trophy_lock();
    for (u32 i = 0; i < SCE_NP_TROPHY_MAX_NUM_TROPHIES; i++) {
        if (trophy_valid(&s_contexts[context], (s32)i) && s_contexts[context].unlocked[i]) {
            u32 word_ea = flags_ea + (i / 32) * 4;
            vm_write32(word_ea, vm_read32(word_ea) | (1u << (i % 32)));
        }
    }

    trophy_unlock();
    vm_write32((u32)(uintptr_t)count, (u32)s_contexts[context].total_trophies);
    return CELL_OK;
}

s32 sceNpTrophyGetGameProgress(SceNpTrophyContext context,
                               SceNpTrophyHandle handle,
                               s32* percentage)
{
    (void)handle;

    if (!s_trophy_initialized)
        return SCE_NP_TROPHY_ERROR_NOT_INITIALIZED;

    if (context <= 0 || context > SCE_NP_TROPHY_MAX_CONTEXTS ||
        !s_contexts[context].in_use)
        return SCE_NP_TROPHY_ERROR_INVALID_CONTEXT;

    if (!s_contexts[context].registered)
        return SCE_NP_TROPHY_ERROR_CONTEXT_NOT_REGISTERED;

    if (!percentage)
        return SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT;

    u32 total = s_contexts[context].total_trophies;
    trophy_lock();
    u32 unlocked = trophy_count_unlocked(&s_contexts[context]);
    trophy_unlock();

    s32 pct = total > 0 ? (s32)((unlocked * 100) / total) : 0;
    vm_write32(GUEST_EA(percentage), (u32)pct);

    printf("[sceNpTrophy] GetGameProgress(ctx=%d) -> %d%%\n",
           context, pct);
    return CELL_OK;
}
