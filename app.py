import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-simple-and-working-secret-key-finally'
socketio = SocketIO(app, async_mode='eventlet')

# --- Constants ---
SECRET_COLORS = ["red", "blue", "green", "yellow", "black", "white"]
GUESS_OPTIONS = SECRET_COLORS + ["empty"]
CODE_LENGTH = 5
NUM_COLOR_PEGS = 4

# --- Game State Class for Stability ---
class GameState:
    def __init__(self):
        self.players = {}
        self.game_started = False
        self.player_order = []
        self.current_turn_sid = None
        self.host_sid = None
        self.guesses = []
        self.secret_code = [] # The full secret code for this round
        print("--- New, Clean GameState created. Server is ready. ---")

    def get_player_list_data(self):
        return [data["name"] for data in self.players.values()]

    def reset_board(self):
        for player_data in self.players.values():
            player_data.pop("secret", None)
            player_data.pop("eliminated", None)
        self.game_started = False
        self.current_turn_sid = None
        self.player_order = []
        self.guesses = []
        self.secret_code = []
        print("--- Game board has been reset. ---")

GAME = GameState()

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    emit('color_list', {'colors': GUESS_OPTIONS})
    if GAME.game_started:
        emit('game_in_progress')

@socketio.on('disconnect')
def handle_disconnect():
    global GAME
    if request.sid in GAME.players:
        player_name = GAME.players.pop(request.sid).get("name", "A player")
        print(f"Player '{player_name}' disconnected.")
        
        if request.sid == GAME.host_sid:
            print("Host disconnected. Full server reset.")
            GAME = GameState()
            emit('game_reset_full', {'message': 'The Host has disconnected. The game has been fully reset.'}, broadcast=True)
        else:
            if GAME.game_started:
                GAME.reset_board()
                emit('game_reset_board', {'message': f'{player_name} left. The game board has been reset.'}, broadcast=True)
            emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('register_player')
def handle_register(data):
    if GAME.game_started: return
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
def handle_start_game():
    if request.sid != GAME.host_sid or GAME.game_started: return
    actual_players_sids = [sid for sid, p_data in GAME.players.items() if not p_data["is_host"]]
    if len(actual_players_sids) < 2: return emit('error', {'message': 'Need at least 2 players to start.'})
        
    GAME.game_started = True
    print("--- Starting Game: Generating 4 colors and 1 empty slot ---")

    # Create the secret code: 4 random colors and 1 empty slot
    GAME.secret_code = random.sample(SECRET_COLORS, NUM_COLOR_PEGS) + ['empty']
    random.shuffle(GAME.secret_code)
    print(f"Secret code created: {GAME.secret_code}")

    # Assign secrets to players based on the colored positions
    color_positions = [i for i, color in enumerate(GAME.secret_code) if color != 'empty']
    random.shuffle(color_positions)
    
    for i, player_sid in enumerate(actual_players_sids):
        if i < len(color_positions):
            pos_index = color_positions[i]
            secret = {"pos": pos_index + 1, "color": GAME.secret_code[pos_index]}
            GAME.players[player_sid]["secret"] = secret
            emit('your_secret', secret, room=player_sid)
            print(f"Assigned to '{GAME.players[player_sid]['name']}': Pos {secret['pos']}, Color {secret['color']}")

    GAME.player_order = actual_players_sids
    random.shuffle(GAME.player_order)
    GAME.current_turn_sid = GAME.player_order[0]
    current_player_name = GAME.players[GAME.current_turn_sid]["name"]
    
    emit('host_overview', {'secret_code': GAME.secret_code}, room=GAME.host_sid)
    emit('game_started', {'turn': current_player_name}, broadcast=True)

@socketio.on('submit_guess')
def handle_guess(data):
    sid = request.sid
    if sid != GAME.current_turn_sid: return
    guess = data.get('guess')
    if not isinstance(guess, list) or len(guess) != CODE_LENGTH: return

    guesser_name = GAME.players[sid]["name"]
    
    # This logic now matches classic Mastermind: black and white pegs for the whole code.
    temp_secret = list(GAME.secret_code)
    temp_guess = list(guess)
    feedback = []

    # First pass for black pegs (correct color in correct position)
    for i in range(CODE_LENGTH):
        if temp_secret[i] != 'empty' and temp_secret[i] == temp_guess[i]:
            feedback.append('black')
            temp_secret[i] = None # Mark as checked in secret
            temp_guess[i] = None  # Mark as checked in guess

    # Second pass for white pegs (correct color in wrong position)
    for i in range(CODE_LENGTH):
        if temp_guess[i] is not None and temp_guess[i] != 'empty':
            if temp_guess[i] in temp_secret:
                feedback.append('white')
                temp_secret.remove(temp_guess[i]) # Remove from secret to prevent double counting
    
    random.shuffle(feedback) # Shuffle feedback to hide position info
    GAME.guesses.append({"guesser": guesser_name, "guess": guess, "feedback": feedback})
    
    if data.get('is_final'):
        is_winner = feedback.count('black') == NUM_COLOR_PEGS and len(feedback) == NUM_COLOR_PEGS

        if is_winner:
            emit('game_over', {'winner': guesser_name, 'secret_code': GAME.secret_code}, broadcast=True)
            GAME.reset_board()
            return
        else:
            GAME.players[sid]['eliminated'] = True
            emit('eliminated', {'name': guesser_name}, broadcast=True)
    
    try:
        current_idx = GAME.player_order.index(sid)
        for i in range(1, len(GAME.player_order) + 1):
            next_sid_candidate = GAME.player_order[(current_idx + i) % len(GAME.player_order)]
            if not GAME.players[next_sid_candidate].get("eliminated"):
                GAME.current_turn_sid = next_sid_candidate
                emit('new_turn', {'last_guess': GAME.guesses[-1], 'next_turn': GAME.players[GAME.current_turn_sid]["name"]}, broadcast=True)
                return
    except (ValueError, IndexError):
        emit('error', {'message': 'Error finding next player. The game may need to be reset.'})
        return
    emit('game_over', {'winner': None}, broadcast=True)

# No if __name__ block for Render deployment
