#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

// ---------- open ----------
int __real_open(const char *pathname, int flags, mode_t mode);
int __wrap_open(const char *pathname, int flags, mode_t mode) {
  int fd = __real_open(pathname, flags, mode);
  printf("[WRAP] open: %s\n", pathname);
  fflush(stdout);
  if (fd == -1) {
    __builtin_trap();
  }
  return fd;
}

// ---------- open64 ----------
int __real_open64(const char *pathname, int flags, mode_t mode);
int __wrap_open64(const char *pathname, int flags, mode_t mode) {
  int fd = __real_open64(pathname, flags, mode);
  printf("[WRAP] open64: %s\n", pathname);
  fflush(stdout);
  if (fd == -1) {
    __builtin_trap();
  }
  return fd;
}

// ---------- openat ----------
int __real_openat(int dirfd, const char *pathname, int flags, mode_t mode);
int __wrap_openat(int dirfd, const char *pathname, int flags, mode_t mode) {
  int fd = __real_openat(dirfd, pathname, flags, mode);
  printf("[WRAP] openat: %s\n", pathname);
  fflush(stdout);
  if (fd == -1) {
    __builtin_trap();
  }
  return fd;
}

// ---------- openat64 ----------
int __real_openat64(int dirfd, const char *pathname, int flags, mode_t mode);
int __wrap_openat64(int dirfd, const char *pathname, int flags, mode_t mode) {
  int fd = __real_openat64(dirfd, pathname, flags, mode);
  printf("[WRAP] openat64: %s\n", pathname);
  fflush(stdout);
  if (fd == -1) {
    __builtin_trap();
  }
  return fd;
}

// ---------- fopen ----------
FILE *__real_fopen(const char *path, const char *mode);
FILE *__wrap_fopen(const char *path, const char *mode) {
  FILE *fp = __real_fopen(path, mode);
  printf("[WRAP] fopen: %s\n", path);
  fflush(stdout);
  if (!fp) {
    __builtin_trap();
  }
  return fp;
}

// ---------- fopen64 ----------
FILE *__real_fopen64(const char *path, const char *mode);
FILE *__wrap_fopen64(const char *path, const char *mode) {
  FILE *fp = __real_fopen64(path, mode);
  printf("[WRAP] fopen64: %s\n", path);
  fflush(stdout);
  if (!fp) {
    __builtin_trap();
  }
  return fp;
}

// ---------- fdopen ----------
FILE *__real_fdopen(int fd, const char *mode);
FILE *__wrap_fdopen(int fd, const char *mode) {
  FILE *fp = __real_fdopen(fd, mode);
  printf("[WRAP] fdopen: fd=%d\n", fd);
  fflush(stdout);
  if (!fp) {
    __builtin_trap();
  }
  return fp;
}

// ---------- freopen ----------
FILE *__real_freopen(const char *path, const char *mode, FILE *stream);
FILE *__wrap_freopen(const char *path, const char *mode, FILE *stream) {
  FILE *fp = __real_freopen(path, mode, stream);
  printf("[WRAP] freopen: %s\n", path ? path : "(null)");
  fflush(stdout);
  if (!fp) {
    __builtin_trap();
  }
  return fp;
}
