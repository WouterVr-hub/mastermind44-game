import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-very-secret-and-stable-key'
socketio = SocketIO(app, async_mode='eventlet')

# --- Game State & Constants ---
SECRET_COLORS = ["red", "blue", "green", "yellow", "black", "white"]
GUESS_OPTIONS = SECRET_COLORS + ["empty"]
CODE_LENGTH = 5
MAX_SECRET_HOLDERS = 4

def create_initial_game_state():
    """Factory function to create a fresh game state dictionary."""
    return {
        "players": {},
        "unassigned_position": None,
        "game_started": False,
        "current_turn_sid": None,
        "player_order": [],
        "guesses": [],
        "host_sid": None
    }

GAME_STATE = create_initial_game_state()

@app.route('/')
def index():
    """Serves the main game page."""
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    """Handles a new client connection."""
    print(f"Client connected: {request.sid}")
    if GAME_STATE["game_started"]:
        emit('game_in_progress')
    emit('color_list', {'colors': GUESS_OPTIONS}, room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    """Handles a client disconnection. Resets the game if a player leaves."""
    ### FIX: Moved `global` to the top of the function ###
    global GAME_STATE
    
    if request.sid in GAME_STATE["players"]:
        player_name = GAME_STATE["players"][request.sid].get("name", "A player")
        print(f"Player '{player_name}' disconnected. Resetting game.")
        
        GAME_STATE = create_initial_game_state()
        emit('game_reset', {'message': f'{player_name} has disconnected. The game has been reset.'}, broadcast=True)

@socketio.on('register_player')
def handle_register(data):
    """Registers a new player or host."""
    if GAME_STATE["game_started"]:
        return emit('error', {'message': 'Game has already started.'})
    
    sid = request.sid
    name = data.get('name', f'Player_{sid[:4]}')
    
    is_host = not GAME_STATE["host_sid"]
    if is_host:
        GAME_STATE["host_sid"] = sid
        name += " (Host)"
    
    GAME_STATE["players"][sid] = {"name": name, "is_host": is_host}
    emit('is_host', {'is_host': is_host}, room=sid)
    
    player_names = [p['name'] for p in GAME_STATE["players"].values()]
    emit('update_player_list', {'players': player_names}, broadcast=True)
    print(f"Player '{name}' registered. Host status: {is_host}")

@socketio.on('start_game')
def handle_start_game():
    """Starts the game, run only by the host."""
    if request.sid != GAME_STATE["host_sid"]:
        return emit('error', {'message': 'Only the host can start the game.'})
    if GAME_STATE["game_started"]:
        return

    actual_players_sids = [sid for sid, data in GAME_STATE["players"].items() if not data["is_host"]]
    if len(actual_players_sids) < 2:
        return emit('error', {'message': 'Need at least 2 players (besides the host) to start.'})
        
    GAME_STATE["game_started"] = True
    print("--- Starting Game: Assigning secrets ---")

    players_to_get_secrets = actual_players_sids[:MAX_SECRET_HOLDERS]
    positions = list(range(1, CODE_LENGTH + 1))
    random.shuffle(positions)
    host_overview = {}

    for player_sid in players_to_get_secrets:
        secret_pos = positions.pop(0)
        secret_color = random.choice(SECRET_COLORS)
        secret = {"pos": secret_pos, "color": secret_color}
        
        GAME_STATE["players"][player_sid]["secret"] = secret
        emit('your_secret', secret, room=player_sid)
        
        host_overview[secret_pos] = secret_color
        print(f"Assigned to '{GAME_STATE['players'][player_sid]['name']}': Pos {secret_pos}, Color {secret_color}")

    GAME_STATE["unassigned_position"] = positions[0]
    print(f"Unassigned Position is: {GAME_STATE['unassigned_position']}")

    GAME_STATE["player_order"] = actual_players_sids
    random.shuffle(GAME_STATE["player_order"])
    GAME_STATE["current_turn_sid"] = GAME_STATE["player_order"][0]
    current_player_name = GAME_STATE["players"][GAME_STATE["current_turn_sid"]]["name"]

    emit('host_overview', {'secrets': host_overview, 'unassigned_pos': GAME_STATE['unassigned_position']}, room=GAME_STATE["host_sid"])
    emit('game_started', {'turn': current_player_name}, broadcast=True)

@socketio.on('submit_guess')
def handle_guess(data):
    """Processes a guess from the current player."""
    ### FIX: Moved `global` to the top for the reset case ###
    global GAME_STATE
    
    sid = request.sid
    if sid != GAME_STATE["current_turn_sid"]:
        return emit('error', {'message': 'It is not your turn.'})
    
    guess = data.get('guess')
    if not isinstance(guess, list) or len(guess) != CODE_LENGTH:
        return emit('error', {'message': 'Invalid guess format.'})

    guesser_name = GAME_STATE["players"][sid]["name"]
    print(f"Guess from '{guesser_name}': {guess}")

    feedback = {}
    for player_sid, player_data in GAME_STATE["players"].items():
        if "secret" in player_data:
            secret = player_data["secret"]
            pos_idx = secret["pos"] - 1
            
            black, white = 0, 0
            if guess[pos_idx] == secret["color"]:
                black = 1
            elif secret["color"] in guess:
                white = 1
            
            feedback[player_sid] = {"black": black, "white": white, "giver": player_data["name"]}
    
    GAME_STATE["guesses"].append({"guesser": guesser_name, "guess": guess, "feedback": feedback})
    
    if data.get('is_final'):
        is_winner = all(fb.get("black") == 1 for fb in feedback.values()) if feedback else False
        if is_winner:
            secret_code = ['empty'] * CODE_LENGTH
            for p_data in GAME_STATE["players"].values():
                if "secret" in p_data:
                    secret_code[p_data["secret"]["pos"] - 1] = p_data["secret"]["color"]
            emit('game_over', {'winner': guesser_name, 'secret_code': secret_code}, broadcast=True)
            
            GAME_STATE = create_initial_game_state()
            return
        else:
            GAME_STATE["players"][sid]['eliminated'] = True
            emit('eliminated', {'name': guesser_name}, broadcast=True)

    # Find next active player
    current_idx = GAME_STATE["player_order"].index(sid)
    next_player_sid = None
    for i in range(1, len(GAME_STATE["player_order"]) + 1):
        next_sid_candidate = GAME_STATE["player_order"][(current_idx + i) % len(GAME_STATE["player_order"])]
        if not GAME_STATE["players"][next_sid_candidate].get("eliminated"):
            next_player_sid = next_sid_candidate
            break
            
    if not next_player_sid:
        emit('game_over', {'winner': None, 'secret_code': []}, broadcast=True)
        GAME_STATE = create_initial_game_state()
        return

    GAME_STATE["current_turn_sid"] = next_player_sid
    next_player_name = GAME_STATE["players"][next_player_sid]["name"]

    emit('new_turn', {'last_guess': GAME_STATE["guesses"][-1], 'next_turn': next_player_name}, broadcast=True)

#if __name__ == '__main__':
#    print("Server starting at http://0.0.0.0:5000")
#    socketio.run(app, host='0.0.0.0', port=5000)