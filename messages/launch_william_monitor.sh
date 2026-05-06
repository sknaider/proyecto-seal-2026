#!/bin/bash
# William's team monitor — tmux session with persistent context bar
SESSION="seal-monitor"

tmux kill-session -t "$SESSION" 2>/dev/null
tmux new-session -d -s "$SESSION" -x 160 -y 35

# Custom status bar: team meter + time
tmux set-option -t "$SESSION" status on
tmux set-option -t "$SESSION" status-interval 5
tmux set-option -t "$SESSION" status-style "bg=#0a0a1a fg=#e0e0e0"
tmux set-option -t "$SESSION" status-left "#[fg=#00ff88,bold] ▲ SEAL #[default]"
tmux set-option -t "$SESSION" status-left-length 12
tmux set-option -t "$SESSION" status-right "#(python3 /home/dadito/IA/proyecto-seal/messages/seal_team_meter.py --tmux 2>/dev/null) #[fg=#888888]%H:%M "
tmux set-option -t "$SESSION" status-right-length 100

# Main pane: watch the full context meter
tmux send-keys -t "$SESSION" "python3 /home/dadito/IA/proyecto-seal/messages/seal_context_meter.py --watch" Enter

echo "[JARVIS] seal-monitor session ready"
echo "Attach: tmux attach -t seal-monitor"
echo "Or open in terminal: tmux attach -t seal-monitor"
