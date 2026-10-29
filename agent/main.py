"""Single autonomous entry point; the official baseline has its own runner."""
import sys

from agent.autonomous import solve


if __name__ == "__main__":
    solve(sys.argv[1], sys.argv[2])
