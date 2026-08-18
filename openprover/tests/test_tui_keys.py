"""Test TUI key handling — run interactively to debug arrow keys.

Usage: python tests/test_tui_keys.py

This sets up a minimal TUI with fake step entries and a confirmation dialog.
Press up/down arrows and observe the output. Press 'q' to quit.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import queue
import select
import termios
import tty


def test_raw_keys():
    """Just read raw bytes from stdin and print what we get."""
    old = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())
    print("Press keys (q to quit). Will show raw bytes:")
    try:
        while True:
            if select.select([sys.stdin], [], [], 0.1)[0]:
                ch = sys.stdin.read(1)
                print(f"  char: {ch!r} (ord={ord(ch)})")
                if ch == 'q':
                    break
                # Check for escape sequence
                if ch == '\x1b':
                    if select.select([sys.stdin], [], [], 0.05)[0]:
                        ch2 = sys.stdin.read(1)
                        print(f"  +char: {ch2!r} (ord={ord(ch2)})")
                        if ch2 == '[':
                            if select.select([sys.stdin], [], [], 0.05)[0]:
                                ch3 = sys.stdin.read(1)
                                print(f"  +char: {ch3!r} (ord={ord(ch3)})")
                                seq = ch + ch2 + ch3
                                print(f"  => full sequence: {seq!r} len={len(seq)}")
                                if ch3 == 'A':
                                    print("  => UP ARROW")
                                elif ch3 == 'B':
                                    print("  => DOWN ARROW")
                    else:
                        print("  => plain ESC")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def test_bg_thread_keys():
    """Test the bg thread + queue approach used by TUI."""
    import threading

    old = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())
    key_queue = queue.Queue()
    stop = False

    def reader():
        while not stop:
            try:
                if select.select([sys.stdin], [], [], 0.04)[0]:
                    ch = sys.stdin.read(1)
                    if not ch:
                        continue
                    if ch == '\x1b':
                        if select.select([sys.stdin], [], [], 0.05)[0]:
                            ch2 = sys.stdin.read(1)
                            if ch2 == '[':
                                if select.select([sys.stdin], [], [], 0.05)[0]:
                                    ch3 = sys.stdin.read(1)
                                    key_queue.put(ch + ch2 + ch3)
                                    continue
                            continue  # unknown escape
                        key_queue.put('\x1b')
                        continue
                    key_queue.put(ch)
            except (OSError, ValueError):
                break

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    print("Press keys (q to quit). Shows what the queue receives:")
    try:
        while True:
            try:
                ch = key_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if len(ch) == 3 and ch[:2] == '\x1b[':
                names = {'A': 'UP', 'B': 'DOWN', 'C': 'RIGHT', 'D': 'LEFT'}
                name = names.get(ch[2], f'?{ch[2]!r}')
                print(f"  queue: {ch!r} => {name} ARROW")
            elif ch == '\x1b':
                print(f"  queue: ESC")
            elif ch == 'q':
                print(f"  queue: 'q' => quitting")
                break
            else:
                print(f"  queue: {ch!r}")
    finally:
        stop = True
        t.join(timeout=0.2)
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def test_tui_confirmation():
    """Full TUI confirmation test with step history."""
    from openprover.tui import TUI

    tui = TUI()
    tui.setup(
        theorem_name="The square root of 2 is irrational",
        work_dir="/tmp/test-tui",
        step_num=3,
        max_steps=10,
    )
    tui.whiteboard = "## Goal\n\nProve sqrt(2) irrational\n\n## Work\n\nAssume p/q..."

    tui.log("Starting proof...", color="cyan")
    tui.step_complete(1, 10, "continue", "Analyzed problem structure", detail="Step 1 detail")
    tui.step_complete(2, 10, "explore_avenue", "Try contradiction", detail="Step 2 detail")
    tui.step_complete(3, 10, "prove_lemma", "Proved p even", detail="Step 3 detail")
    tui.show_proposal({
        "action": "verify",
        "summary": "Verify the main argument",
        "reasoning": "We have all pieces",
    })

    try:
        result = tui.get_confirmation()
        tui.log(f"Got: '{result}'", color="green")
        import time; time.sleep(0.5)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        tui.cleanup()
        print(f"Done")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("test", choices=["raw", "queue", "tui"], default="queue", nargs="?")
    args = parser.parse_args()

    if args.test == "raw":
        test_raw_keys()
    elif args.test == "queue":
        test_bg_thread_keys()
    elif args.test == "tui":
        test_tui_confirmation()
