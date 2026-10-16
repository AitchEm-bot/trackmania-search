// SearchBridge: lets a Python search process drive TrackMania Nations Forever through TMInterface.
//
// The plugin keeps game save states in memory and exposes a tiny TCP protocol on 127.0.0.1.
// Python asks it to "expand" a stored state: rewind to it, hold a set of inputs for N physics
// ticks (10 ms each), then store the resulting state and report where the car ended up.
//
// Wire format: little-endian. Every message starts with an int32 type.

const string HOST = "127.0.0.1";
const uint16 PORT = 8477;
const uint IDLE_TIMEOUT_MS = 15000;

// Client -> plugin
const int C_PING = 1;
const int C_SET_SPEED = 2;      // float speed
const int C_WAIT_START = 3;     // uint8 restart; replies with the root node once RaceTime >= 0
const int C_EXPAND = 4;         // int32 parent, int32 ticks, uint8 up, uint8 down, uint8 left, uint8 right, int32 steer
const int C_FREE = 5;           // int32 n, int32 ids[n]
const int C_EXEC = 6;           // int32 len, bytes command
const int C_GET_BLOCKS = 7;
const int C_DETACH = 8;         // stop blocking the game loop
const int C_GAME_STATE = 9;     // replies S_STATE int32 TM::GameState, int32 race time, uint8 in race
const int C_GET_VAR = 10;       // int32 len, bytes name; replies S_TEXT with the value as text
const int C_SCREENSHOT = 11;    // int32 w, int32 h; replies S_FRAME int32 w, int32 h, int32 n, bytes (captured in Render)
const int C_FOCUS = 12;         // brings the game window to the front (the game needs focus once after launch)
const int C_RECORD = 13;        // int32 on, int32 w, int32 h: stream one S_TICK_FRAME per new race tick from Render
const int C_RELEASE = 14;       // let the race run on its own (e.g. driven by a loaded TMInterface script)

// Plugin -> client
const int S_PONG = 100;
const int S_NODE = 101;
const int S_BLOCKS = 102;
const int S_OK = 103;
const int S_STATE = 104;
const int S_TEXT = 105;
const int S_FRAME = 106;
const int S_TICK_FRAME = 107;   // int32 race time, int32 display speed, int32 w, int32 h, int32 n, bytes
const int S_FINISH = 108;       // int32 finish time (sent while recording)

int frameW = -1;
int frameH = -1;

bool recording = false;
int recordW = 640;
int recordH = 480;
int lastRecordedTime = -100000;

void WriteText(const string&in text)
{
    client.Write(S_TEXT);
    client.Write(int(text.Length));
    client.Write(text);
}

const int MODE_IDLE = 0;
const int MODE_WAIT_START = 1;
const int MODE_STEPPING = 2;
const int MODE_SERVING = 3;

Net::Socket@ server = null;
Net::Socket@ client = null;

array<SimulationState@> states;
array<int> freeIds;
SimulationState@ rootState = null;   // race start, used to park the car when the client lets go

int mode = MODE_IDLE;
bool pendingGiveUp = false;
bool needCountdown = false;   // after a restart, wait until the countdown is seen before taking the root
bool needPark = false;        // park at the start line for one tick before giving up
bool guardFinish = false;     // prevent finishes for the rest of this race (set once a client takes control)
int stepsLeft = 0;

// Inputs held during the current segment.
bool inUp = false;
bool inDown = false;
bool inLeft = false;
bool inRight = false;
int inSteer = 0;

// Events observed during the current segment.
bool segWall = false;
int segFinish = -1;

int StoreState(SimulationState@ s)
{
    if (freeIds.Length > 0) {
        int id = freeIds[freeIds.Length - 1];
        freeIds.RemoveAt(freeIds.Length - 1);
        @states[id] = s;
        return id;
    }
    states.Add(s);
    return int(states.Length) - 1;
}

void FreeState(int id)
{
    if (id < 0 || id >= int(states.Length) || states[id] is null) {
        return;
    }
    @states[id] = null;
    freeIds.Add(id);
}

void ApplyInputs(SimulationManager@ sm)
{
    sm.SetInputState(InputType::Up, inUp ? 1 : 0);
    sm.SetInputState(InputType::Down, inDown ? 1 : 0);
    if (inSteer != 0) {
        sm.SetInputState(InputType::Left, 0);
        sm.SetInputState(InputType::Right, 0);
        sm.SetInputState(InputType::Steer, inSteer);
    } else {
        sm.SetInputState(InputType::Steer, 0);
        sm.SetInputState(InputType::Left, inLeft ? 1 : 0);
        sm.SetInputState(InputType::Right, inRight ? 1 : 0);
    }
}

uint8 WheelsOnGround(SimulationManager@ sm)
{
    uint8 n = 0;
    for (uint i = 0; i < 4; i++) {
        if (sm.Wheels[i].RTState.HasGroundContact) {
            n++;
        }
    }
    return n;
}

bool AnyWheelSliding(SimulationManager@ sm)
{
    for (uint i = 0; i < 4; i++) {
        if (sm.Wheels[i].RTState.IsSliding) {
            return true;
        }
    }
    return false;
}

void SendNode(SimulationManager@ sm, int id)
{
    TM::HmsStateDyna@ dyna = sm.Dyna.RefStateCurrent;
    vec3 pos = dyna.Location.Position;
    vec3 vel = dyna.LinearSpeed;
    float yaw, pitch, roll;
    dyna.Location.Rotation.GetYawPitchRoll(yaw, pitch, roll);

    client.Write(S_NODE);
    client.Write(id);
    client.Write(sm.RaceTime);
    client.Write(pos.x);
    client.Write(pos.y);
    client.Write(pos.z);
    client.Write(vel.x);
    client.Write(vel.y);
    client.Write(vel.z);
    client.Write(yaw);
    client.Write(pitch);
    client.Write(roll);
    client.Write(int(sm.PlayerInfo.CurCheckpointCount));
    client.Write(segFinish);
    client.Write(uint8(segWall ? 1 : 0));
    client.Write(WheelsOnGround(sm));
    client.Write(uint8(AnyWheelSliding(sm) ? 1 : 0));
    client.Write(int(sm.PlayerInfo.DisplaySpeed));
}

void SendBlocks()
{
    TM::GameCtnChallenge@ challenge = GetCurrentChallenge();
    client.Write(S_BLOCKS);
    if (challenge is null) {
        client.Write(int(0));
        return;
    }
    auto blocks = challenge.Blocks;  // an explicit array<TM::GameCtnBlock@>@ declaration does not compile in TMI 2.2.1
    client.Write(int(blocks.Length));
    for (uint i = 0; i < blocks.Length; i++) {
        string name = blocks[i].Name;
        client.Write(int(name.Length));
        client.Write(name);
        client.Write(int(blocks[i].Coord.x));
        client.Write(int(blocks[i].Coord.y));
        client.Write(int(blocks[i].Coord.z));
        client.Write(int(blocks[i].Dir));
        client.Write(int(blocks[i].WayPointType));
    }
}

// Never leave the car in a searched state when the client lets go: a finished state would be
// registered by the game as a real (tool-assisted) finish. Park it at the race start instead.
void ParkAtStart(SimulationManager@ sm, bool inRace)
{
    if (!inRace || rootState is null) {
        return;
    }
    sm.RewindToState(rootState, false);
    sm.PreventSimulationFinish();
    inUp = false;
    inDown = false;
    inLeft = false;
    inRight = false;
    inSteer = 0;
    ApplyInputs(sm);
}

void Disconnect(const string&in reason)
{
    log("SearchBridge: client dropped (" + reason + ")", Severity::Warning);
    @client = null;
    mode = MODE_IDLE;
}

// Handles one command. Returns true if the command advances game time (so the caller must
// return from the game callback and let physics run).
bool HandleCommand(SimulationManager@ sm, int type, bool inRace)
{
    switch (type) {
        case C_PING: {
            client.Write(S_PONG);
            return false;
        }
        case C_SET_SPEED: {
            float speed = client.ReadFloat();
            sm.SetSpeed(speed);
            client.Write(S_OK);
            return false;
        }
        case C_EXEC: {
            int len = client.ReadInt32();
            string cmd = client.ReadString(len);
            ExecuteCommand(cmd, ExecuteCommandFlags::SuppressOutput);
            client.Write(S_OK);
            return false;
        }
        case C_GET_BLOCKS: {
            SendBlocks();
            return false;
        }
        case C_RECORD: {
            recording = client.ReadInt32() != 0;
            recordW = client.ReadInt32();
            recordH = client.ReadInt32();
            lastRecordedTime = -100000;
            client.Write(S_OK);
            return false;
        }
        case C_RELEASE: {
            mode = MODE_IDLE;
            client.Write(S_OK);
            return true;
        }
        case C_FOCUS: {
            Graphics::ShowGameWindow();
            Graphics::FocusGameWindow();
            client.Write(S_OK);
            return false;
        }
        case C_SCREENSHOT: {
            frameW = client.ReadInt32();
            frameH = client.ReadInt32();
            return false;
        }
        case C_GET_VAR: {
            int len = client.ReadInt32();
            string name = client.ReadString(len);
            string s;
            double d;
            bool b;
            if (GetVariable(name, s)) {
                WriteText(s);
            } else if (GetVariable(name, d)) {
                WriteText("" + d);
            } else if (GetVariable(name, b)) {
                WriteText(b ? "true" : "false");
            } else {
                WriteText("<unknown>");
            }
            return false;
        }
        case C_GAME_STATE: {
            client.Write(S_STATE);
            client.Write(int(GetCurrentGameState()));
            client.Write(sm.RaceTime);
            client.Write(uint8(sm.InRace ? 1 : 0));
            return false;
        }
        case C_FREE: {
            int n = client.ReadInt32();
            for (int i = 0; i < n; i++) {
                FreeState(client.ReadInt32());
            }
            client.Write(S_OK);
            return false;
        }
        case C_DETACH: {
            ParkAtStart(sm, inRace);
            client.Write(S_OK);
            @client = null;
            mode = MODE_IDLE;
            return true;
        }
        case C_WAIT_START: {
            uint8 flag = client.ReadUint8();   // 0 = no restart, 1 = restart, 2 = restart keeping the loaded script
            bool restart = flag != 0;
            // A loaded input script (TMInterface loads one after a finished run) overrides the inputs
            // set by this plugin, and SetInputState only takes effect while execute_commands is on.
            if (flag != 2) {
                ExecuteCommand("unload", ExecuteCommandFlags::SuppressOutput);
            }
            ExecuteCommand("set execute_commands true", ExecuteCommandFlags::SuppressOutput);
            guardFinish = true;
            pendingGiveUp = restart;
            needCountdown = restart;
            needPark = restart && rootState !is null;
            mode = MODE_WAIT_START;
            return true;
        }
        case C_EXPAND: {
            int parent = client.ReadInt32();
            int ticks = client.ReadInt32();
            inUp = client.ReadUint8() != 0;
            inDown = client.ReadUint8() != 0;
            inLeft = client.ReadUint8() != 0;
            inRight = client.ReadUint8() != 0;
            inSteer = client.ReadInt32();
            if (!inRace || parent < 0 || parent >= int(states.Length) || states[parent] is null || ticks < 1) {
                log("SearchBridge: invalid expand (parent " + parent + ")", Severity::Error);
                Disconnect("invalid expand");
                return true;
            }
            sm.RewindToState(states[parent], false);
            sm.PreventSimulationFinish();
            ApplyInputs(sm);
            stepsLeft = ticks;
            segWall = false;
            segFinish = -1;
            mode = MODE_STEPPING;
            return true;
        }
    }
    log("SearchBridge: unknown message " + type, Severity::Error);
    Disconnect("protocol error");
    return true;
}

// Serves commands until one of them advances game time. While serving, the game is paused
// inside this call, which keeps the search in lock-step with the physics.
void Serve(SimulationManager@ sm, bool inRace)
{
    mode = MODE_SERVING;
    uint64 waitStart = Time::Now;
    while (client !is null) {
        if (client.Available < 4) {
            if (Time::Now - waitStart > IDLE_TIMEOUT_MS) {
                log("SearchBridge: no command for a while, releasing the game", Severity::Warning);
                ParkAtStart(sm, inRace);
                mode = MODE_IDLE;
                return;
            }
            continue;
        }
        int type = client.ReadInt32();
        if (HandleCommand(sm, type, inRace)) {
            return;
        }
        waitStart = Time::Now;
    }
}

void OnRunStep(SimulationManager@ sm)
{
    if (client is null) {
        return;
    }

    if (pendingGiveUp) {
        // Giving up from a searched state that crossed the finish makes the game register that
        // finish, so first go back to the start line and let one tick run from there. (Rewinding
        // and giving up in the same step leaves the restarted race unable to accelerate.)
        if (needPark) {
            needPark = false;
            ParkAtStart(sm, true);
            return;
        }
        pendingGiveUp = false;
        sm.GiveUp();
        return;
    }

    if (mode == MODE_WAIT_START) {
        if (sm.RaceTime < 0) {
            needCountdown = false;
            return;
        }
        if (needCountdown) {
            return;
        }
        sm.PreventSimulationFinish();
        segWall = false;
        segFinish = -1;
        guardFinish = true;
        @rootState = sm.SaveState();
        SendNode(sm, StoreState(sm.SaveState()));
        Serve(sm, true);
        return;
    }

    if (mode == MODE_STEPPING) {
        if (sm.SceneVehicleCar.HasAnyLateralContact) {
            segWall = true;
        }
        stepsLeft--;
        if (stepsLeft > 0) {
            ApplyInputs(sm);
            return;
        }
        SendNode(sm, StoreState(sm.SaveState()));
        Serve(sm, true);
        return;
    }

    if (mode == MODE_SERVING || client.Available >= 4) {
        Serve(sm, true);
    }
}

void OnGameStateChanged(TM::GameState state)
{
    // A saved start state only belongs to the race it was taken in.
    if (state != TM::GameState::LocalRace) {
        @rootState = null;
        guardFinish = false;
    }
}

void OnCheckpointCountChanged(SimulationManager@ sm, int current, int target)
{
    // guardFinish stays on for the rest of the race once a client has taken control, so a script or
    // searched state can never finish for real even if the client disconnects (e.g. plugin reload).
    if (current != target || (client is null && !guardFinish)) {
        return;
    }
    if (mode == MODE_STEPPING && segFinish < 0) {
        segFinish = sm.RaceTime;
    }
    if (recording) {
        client.Write(S_FINISH);
        client.Write(sm.RaceTime);
    }
    // Must be called at the moment the finish is crossed; otherwise the game registers the
    // (tool-assisted) finish as a real record and autosaves its replay.
    sm.PreventSimulationFinish();
}

void Render()
{
    if (server is null) {
        if (Time::Now - loadedAt > 3000) {   // fallback if the queued sb_listen command never ran
            StartServer();
        }
        return;
    }
    // A new connection always replaces the old one (the old client may have gone away silently).
    Net::Socket@ incoming = server.Accept(0);
    if (incoming !is null) {
        @client = incoming;
        client.NoDelay = true;
        mode = MODE_IDLE;
        recording = false;
        frameW = -1;
        log("SearchBridge: client connected", Severity::Success);
    }
    if (client is null) {
        return;
    }
    // Recording: one frame per new race tick (run the game slowly so every tick gets rendered). Works both
    // while the race runs on its own and while an expansion is stepping (frames arrive before the node).
    if (recording && (mode == MODE_IDLE || mode == MODE_STEPPING)) {
        SimulationManager@ rsm = GetSimulationManager();
        if (rsm.InRace && rsm.RaceTime >= 0 && rsm.RaceTime != lastRecordedTime) {
            lastRecordedTime = rsm.RaceTime;
            vec2 size(recordW, recordH);
            auto pixels = Graphics::CaptureScreenshot(size);
            client.Write(S_TICK_FRAME);
            client.Write(rsm.RaceTime);
            client.Write(int(rsm.PlayerInfo.DisplaySpeed));
            client.Write(int(size.x));
            client.Write(int(size.y));
            client.Write(int(pixels.Length));
            client.Write(pixels);
        }
    }
    if (frameW > 0) {
        vec2 size(frameW, frameH);
        auto pixels = Graphics::CaptureScreenshot(size);
        client.Write(S_FRAME);
        client.Write(int(size.x));
        client.Write(int(size.y));
        client.Write(int(pixels.Length));
        client.Write(pixels);
        frameW = -1;
    }
    // Outside a race (menus, loading) there are no run steps, so serve simple commands here.
    if (mode == MODE_IDLE && client.Available >= 4) {
        SimulationManager@ sm = GetSimulationManager();
        int type = client.ReadInt32();
        HandleCommand(sm, type, false);
    }
}

uint64 loadedAt = 0;

void StartServer()
{
    if (server !is null) {
        return;
    }
    uint16 port = uint16(GetVariableDouble("sb_port"));
    @server = Net::Socket();
    if (!server.Listen(HOST, port)) {
        log("SearchBridge: could not listen on port " + port, Severity::Error);
        @server = null;
        return;
    }
    log("SearchBridge: listening on " + HOST + ":" + port, Severity::Success);
}

void OnListenCommand(int fromTime, int toTime, const string&in commandLine, const array<string>&in args)
{
    StartServer();
}

void Main()
{
    // If the plugin is (re)loaded while one of our input scripts is driving, nothing would stop that
    // run from finishing for real, so stop any loaded script.
    ExecuteCommand("unload", ExecuteCommandFlags::SuppressOutput);
    // The port can be set per game instance at launch (/configstring="set sb_port 8478"). Launch-time
    // commands run after plugins load, so start listening from a queued command that runs after them.
    RegisterVariable("sb_port", double(PORT));
    RegisterCustomCommand("sb_listen", "Start the SearchBridge server", OnListenCommand);
    CommandList cmdList;
    cmdList.Content = "sb_listen";
    cmdList.Process();
    loadedAt = Time::Now;
}

void OnDisabled()
{
    @client = null;
    @server = null;
}

PluginInfo@ GetPluginInfo()
{
    PluginInfo info;
    info.Author = "CSCI218 group";
    info.Name = "SearchBridge";
    info.Description = "Exposes save/rewind/step to an external search process over TCP";
    info.Version = "0.1";
    return info;
}
