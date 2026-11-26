#ifndef PS3RECOMP_FS_HOST_OPEN_H
#define PS3RECOMP_FS_HOST_OPEN_H

#include "fs_open_flags.h"
#include "../../include/ps3emu/error_codes.h"

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/stat.h>
#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h>
#endif

/* Apply creation and truncation in a single native open. fdopen only binds
 * stdio to the descriptor and does not create or truncate the file. */
static inline FILE* fs_host_open(const char* path, uint32_t flags)
{
    int access = flags & CELL_FS_O_ACCMODE;
    int append = (flags & CELL_FS_O_APPEND) != 0;
    int native_flags;
    const char* stream_mode;

    switch (access) {
    case CELL_FS_O_RDONLY:
        if (flags & CELL_FS_O_TRUNC) {
            errno = EINVAL;
            return NULL;
        }
        native_flags = O_RDONLY;
        stream_mode = "rb";
        break;
    case CELL_FS_O_WRONLY:
        native_flags = O_WRONLY;
        stream_mode = append ? "ab" : "wb";
        break;
    case CELL_FS_O_RDWR:
        native_flags = O_RDWR;
        stream_mode = append ? "a+b" : "r+b";
        break;
    default:
        errno = EINVAL;
        return NULL;
    }
    if (flags & CELL_FS_O_CREAT) {
        native_flags |= O_CREAT;
        if (flags & CELL_FS_O_EXCL)
            native_flags |= O_EXCL;
    }
    if (flags & CELL_FS_O_TRUNC)
        native_flags |= O_TRUNC;
    if (append && access != CELL_FS_O_RDONLY)
        native_flags |= O_APPEND;

    /* Guest probes (including bare device roots) expect CELL_EISDIR. */
#ifdef _WIN32
    struct __stat64 st;
    if (_stat64(path, &st) == 0 && (st.st_mode & _S_IFMT) == _S_IFDIR) {
#else
    struct stat st;
    if (stat(path, &st) == 0 && S_ISDIR(st.st_mode)) {
#endif
        errno = EISDIR;
        return NULL;
    }

#ifdef _WIN32
    int fd = _open(path, native_flags | _O_BINARY, _S_IREAD | _S_IWRITE);
    if (fd < 0)
        return NULL;
    FILE* fp = _fdopen(fd, stream_mode);
#else
    int fd = open(path, native_flags, 0666);
    if (fd < 0)
        return NULL;
    FILE* fp = fdopen(fd, stream_mode);
#endif
    if (!fp) {
        int error = errno;
#ifdef _WIN32
        _close(fd);
#else
        close(fd);
#endif
        errno = error;
    }
    return fp;
}

static inline int32_t fs_host_open_error(int error)
{
    switch (error) {
    case ENOENT:       return (int32_t)CELL_ENOENT;
    case EEXIST:       return (int32_t)CELL_EEXIST;
    case EISDIR:       return (int32_t)CELL_EISDIR;
    case ENOTDIR:      return (int32_t)CELL_ENOTDIR;
    case EACCES:       return (int32_t)CELL_EACCES;
    case EPERM:        return (int32_t)CELL_EPERM;
    case EBUSY:        return (int32_t)CELL_EBUSY;
    case EROFS:        return (int32_t)CELL_EROFS;
    case EINVAL:       return (int32_t)CELL_EINVAL;
    case ENAMETOOLONG: return (int32_t)CELL_ENAMETOOLONG;
    case EMFILE:       return (int32_t)CELL_EMFILE;
    case ENFILE:       return (int32_t)CELL_ENFILE;
    case ENOMEM:       return (int32_t)CELL_ENOMEM;
    case ENOSPC:       return (int32_t)CELL_ENOSPC;
    default:           return (int32_t)CELL_EIO;
    }
}

#endif
