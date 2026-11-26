#ifndef PS3RECOMP_FS_OPEN_FLAGS_H
#define PS3RECOMP_FS_OPEN_FLAGS_H

/* PS3 guest ABI. The host O_* flags have different values. */
#define CELL_FS_O_RDONLY    000000
#define CELL_FS_O_WRONLY    000001
#define CELL_FS_O_RDWR      000002
#define CELL_FS_O_ACCMODE   000003
#define CELL_FS_O_CREAT     000100
#define CELL_FS_O_EXCL      000200
#define CELL_FS_O_TRUNC     001000
#define CELL_FS_O_APPEND    002000
#define CELL_FS_O_MSELF     010000

static inline int fs_read_only(int flags)
{
    return (flags & (CELL_FS_O_ACCMODE | CELL_FS_O_CREAT | CELL_FS_O_TRUNC |
                     CELL_FS_O_APPEND)) == CELL_FS_O_RDONLY;
}

#endif
