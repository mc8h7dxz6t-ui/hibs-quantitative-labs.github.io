"""Terminal dashboard (curses) for queue monitoring."""

from __future__ import annotations

import curses
import threading
import time

from media_suite.config import OUTPUT_DIR, WATCH_FILE
from media_suite.daemon import DaemonState, watch_folder_daemon


def _safe_addstr(win, y: int, x: int, text: str, attr=0) -> None:
    height, width = win.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    win.addstr(y, x, text[: max(0, width - x - 1)], attr)


def draw_dashboard(stdscr, state: DaemonState, stop_event: threading.Event) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)

    daemon = threading.Thread(
        target=watch_folder_daemon,
        kwargs={"state": state, "stop_event": stop_event},
        daemon=True,
    )
    daemon.start()

    while not stop_event.is_set():
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        _safe_addstr(stdscr, 1, 2, "=" * min(78, w - 4), curses.color_pair(1) | curses.A_BOLD)
        _safe_addstr(
            stdscr,
            2,
            2,
            "     FORENSIC MEDIA SUITE — Apple Silicon / FFmpeg Gold Standard     ",
            curses.color_pair(1) | curses.A_BOLD,
        )
        _safe_addstr(stdscr, 3, 2, "=" * min(78, w - 4), curses.color_pair(1) | curses.A_BOLD)

        with state.lock:
            status = state.status
            job = state.current_job
            count = state.processed_count
            fps = state.live_fps
            speed = state.live_speed

        ok = "Verified" in status or "Awaiting" in status
        _safe_addstr(stdscr, 5, 4, "Engine status        : ")
        _safe_addstr(stdscr, 5, 27, status, curses.color_pair(2 if ok else 3) | curses.A_BOLD)

        _safe_addstr(stdscr, 7, 4, "Active track         : ")
        _safe_addstr(stdscr, 7, 27, job, curses.A_DIM)

        _safe_addstr(stdscr, 9, 4, "Encode FPS           : ")
        _safe_addstr(stdscr, 9, 27, f"{fps}", curses.color_pair(2))

        _safe_addstr(stdscr, 10, 4, "Processing speed     : ")
        _safe_addstr(stdscr, 10, 27, speed, curses.color_pair(2))

        _safe_addstr(stdscr, 12, 4, "Signed assets        : ")
        _safe_addstr(stdscr, 12, 27, str(count))

        _safe_addstr(stdscr, 14, 4, "Queue file           : ")
        _safe_addstr(stdscr, 14, 27, str(WATCH_FILE.resolve()), curses.A_UNDERLINE)

        _safe_addstr(stdscr, 15, 4, "Output directory     : ")
        _safe_addstr(stdscr, 15, 27, str(OUTPUT_DIR.resolve()) + "/")

        _safe_addstr(stdscr, h - 2, 2, "Press 'q' to quit")

        stdscr.refresh()
        try:
            if stdscr.getch() == ord("q"):
                stop_event.set()
                break
        except curses.error:
            pass
        time.sleep(0.25)


def run_dashboard() -> None:
    state = DaemonState()
    stop_event = threading.Event()

    def wrapper(stdscr):
        draw_dashboard(stdscr, state, stop_event)

    curses.wrapper(wrapper)
