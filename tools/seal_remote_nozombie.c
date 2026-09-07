#define _GNU_SOURCE

#include <dlfcn.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/*
 * seal-remote's Linux --server worker launches short-lived helper children but
 * does not wait(2) for them.  The vendor process therefore retains one zombie
 * after every hourly worker refresh.  Apply SA_NOCLDWAIT only to the --server
 * worker; the --service and --tray processes keep their original semantics.
 */

static bool target_server = false;

typedef int (*sigaction_fn)(int, const struct sigaction *, struct sigaction *);

static sigaction_fn real_sigaction(void) {
    static sigaction_fn function = NULL;
    if (function == NULL) {
        function = (sigaction_fn)dlsym(RTLD_NEXT, "sigaction");
    }
    return function;
}

static bool cmdline_contains(const char *needle) {
    FILE *stream = fopen("/proc/self/cmdline", "rb");
    if (stream == NULL) {
        return false;
    }

    char buffer[8192];
    const size_t length = fread(buffer, 1, sizeof(buffer) - 1, stream);
    fclose(stream);
    buffer[length] = '\0';

    for (size_t index = 0; index < length; index++) {
        if (buffer[index] == '\0') {
            buffer[index] = ' ';
        }
    }
    return strstr(buffer, needle) != NULL;
}

static struct sigaction no_zombie_action(void) {
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = SIG_IGN;
    action.sa_flags = SA_NOCLDWAIT | SA_RESTART;
    sigemptyset(&action.sa_mask);
    return action;
}

static void *enforce_child_reaping(void *unused) {
    (void)unused;
    const struct timespec interval = {.tv_sec = 0, .tv_nsec = 250000000};
    for (;;) {
        const struct sigaction action = no_zombie_action();
        sigaction_fn function = real_sigaction();
        if (function != NULL) {
            (void)function(SIGCHLD, &action, NULL);
        }
        (void)nanosleep(&interval, NULL);
    }
    return NULL;
}

/* Keep the vendor worker from resetting SIGCHLD to SIG_DFL after startup. */
int sigaction(int signum, const struct sigaction *action, struct sigaction *old_action) {
    sigaction_fn function = real_sigaction();
    if (function == NULL) {
        return -1;
    }
    if (target_server && signum == SIGCHLD && action != NULL) {
        const struct sigaction forced = no_zombie_action();
        return function(signum, &forced, old_action);
    }
    return function(signum, action, old_action);
}

__attribute__((constructor)) static void seal_remote_enable_child_reaping(void) {
    const char *force = getenv("SEAL_REMOTE_NOZOMBIE_FORCE");
    if (!cmdline_contains("--server") && !(force && strcmp(force, "1") == 0)) {
        return;
    }
    target_server = true;
    const struct sigaction action = no_zombie_action();
    (void)sigaction(SIGCHLD, &action, NULL);
    pthread_t thread;
    if (pthread_create(&thread, NULL, enforce_child_reaping, NULL) == 0) {
        (void)pthread_detach(thread);
    }
}
