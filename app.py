import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-truly-stable-secret-key-that-works'
socketio = SocketIO(app, async_mode='eventlet')

# --- Constants ---
SECRET_COLORS = ["red", "blue", "green", "yellow", "black", "white"]
GUESS_OPTIONS = SECRET_COLORS + ["empty"]
CODE_LENGTH = 5
MAX_SECRET_HOLDERS = 4

# --- Game State Management using a Class for Stability ---
class GameState:
    def __init__(self):
        self.players = {}  # {sid: {"name": str, "is_host": bool, "secret": dict}}
        self.game_started = False
        self.player_order = []
        self.current_turn_sid = None
        self.host_sid = None
        self.guesses = []
        print("--- New GameState created. Server is ready. ---")

    def get_player_list_data(self):
        """Returns a list of player data safe to send to clients."""
        return [{"sid": sid, "name": data["name"], "is_host": data["is_host"]} for sid, data in self.players.items()]

    def reset_board(self):
        """Resets the game board but keeps all players."""
        for player_data in self.players.values():
            player_data.pop("secret", None)
            player_data.pop("eliminated", None)
        self.game_started = False
        self.current_turn_sid = None
        self.player_order = []
        self.guesses = []
        print("--- Game board has been reset by host ---")

GAME = GameState()

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    # Immediately send crucial data to the newly connected client.
    emit('color_list', {'colors': GUESS_OPTIONS, 'secret_colors': SECRET_COLORS})
    if GAME.game_started:
        emit('game_in_progress')

@socketio.on('disconnect')
def handle_disconnect():
    global GAME
    if request.sid in GAME.players:
        player_name = GAME.players.pop(request.sid).get("name", "A player")
        print(f"Player '{player_name}' with SID {request.sid} disconnected.")

        # If the host disconnects, the entire game must be reset for everyone.
        if request.sid == GAME.host_sid:
            print("Host disconnected. A full server reset is triggered.")
            GAME = GameState()
            emit('game_reset_full', {'message': 'The Host has disconnected. The game has been fully reset.'}, broadcast=True)
        # If a player leaves during a game, reset the board.
        elif GAME.game_started:
            print("A player left mid-game. Resetting the board.")
            GAME.reset_board()
            emit('game_reset_board', {'message': f'{player_name} left. The game board has been reset.'}, broadcast=True)
            emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)
        # If a player leaves before the game, just update the player list.
        else:
            emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('register_player')
def handle_register(data):
    if GAME.game_started:
        return emit('error', {'message': 'Game has already started.'})
    
    sid = request.sid
    name = data.get('name', f'Player_{sid[:4]}')
    is_host = not GAME.host_sid
    
    if is_host:
        GAME.host_sid = sid
        name += " (Host)"
    
    GAME.players[sid] = {"name": name, "is_host": is_host}
    emit('is_host', {'is_host': is_host})
    print(f"Player '{name}' registered. Host status: {is_host}")
    emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('reset_game_by_host')
def handle_reset_by_host():
    if request.sid == GAME.host_sid:
        GAME.reset_board()
        emit('game_reset_board', {'message': 'The Host has reset the game board.'}, broadcast=True)
        emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('start_game')
def handle_start_game(data):
    if request.sid != GAME.host_sid or GAME.game_started: return
    
    secrets_from_host = data.get('secrets')
    unassigned_pos = data.get('unassigned_pos')
    if not secrets_from_host or not unassigned_pos:
        return emit('error', {'message': 'Invalid start game data from host.'})

    actual_players_sids = [sid for sid, p_data in GAME.players.items() if not p_data["is_host"]]
    if len(actual_players_sids) < 2:
        return emit('error', {'message': 'Need at least 2 players to start.'})
        
    GAME.game_started = True
    print("--- Starting Game with Host-Defined Secrets ---")

    for assignment in secrets_from_host:
        player_sid = assignment["sid"]
        if player_sid in GAME.players:
            secret = {"pos": assignment["pos"], "color": assignment["color"]}
            GAME.players[player_sid]["secret"] = secret
            emit('your_secret', secret, room=player_sid)
            print(f"Assigned to '{GAME.players[player_sid]['name']}': Pos {secret['pos']}, Color {secret['color']}")

    GAME.player_order = actual_players_sids
    random.shuffle(GAME.player_order)
    GAME.current_turn_sid = GAME.player_order[0]
    current_player_name = GAME.players[GAME.current_turn_sid]["name"]
    
    emit('game_started', {'turn': current_player_name}, broadcast=True)

# The submit_guess function does not need changes, but it is included here for completeness.
@socketio.on('submit_guess')
def handle_guess(data):
    sid = request.sid
    if sid != GAME.current_turn_sid: return
    guess = data.get('guess')
    if not isinstance(guess, list) or len(guess) != CODE_LENGTH: return

    guesser_name = GAME.players[sid]["name"]
    feedback = {}
    for player_sid, player_data in GAME.players.items():
        if "secret" in player_data:
            secret = player_data["secret"]
            pos_idx = secret["pos"] - 1
            black, white = (1, 0) if guess[pos_idx] == secret["color"] else (0, 1) if secret["color"] in guess else (0, 0)
            feedback[player_sid] = {"black": black, "white": white, "giver": player_data["name"]}
    GAME.guesses.append({"guesser": guesser_name, "guess": guess, "feedback": feedback})
    
    if data.get('is_final'):
        is_winner = all(fb.get("black") == 1 for fb in feedback.values()) if feedback else False
        if is_winner:
            secret_code = ['empty'] * CODE_LENGTH
            for p_data in GAME.players.values():
                if "secret" in p_data: secret_code[p_data["secret"]["pos"] - 1] = p_data["secret"]["color"]
            emit('game_over', {'winner': guesser_name, 'secret_code': secret_code}, broadcast=True)
            GAME.reset_board()
            return
        else:
            GAME.players[sid]['eliminated'] = True
            emit('eliminated', {'name': guesser_name}, broadcast=True)
    
    current_idx = GAME.player_order.index(sid)
    for i in range(1, len(GAME.player_order) + 1):
        next_sid_candidate = GAME.player_order[(current_idx + i) % len(GAME.player_order)]
        if not GAME.players[next_sid_candidate].get("eliminated"):
            GAME.current_turn_sid = next_sid_candidate
            emit('new_turn', {'last_guess': GAME.guesses[-1], 'next_turn': GAME.players[GAME.current_turn_sid]["name"]}, broadcast=True)
            return
    emit('game_over', {'winner': None}, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
