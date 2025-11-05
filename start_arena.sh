#!/bin/bash
# Alpha Arena Startup Script with Auto-Install and Frontend Build

# Check for stop parameter
if [ "$1" = "stop" ]; then
    echo "=== Stopping Alpha Arena ==="

    # Kill processes using port 8802 (most precise)
    if command -v lsof &> /dev/null; then
        PIDS=$(lsof -t -i:8802 2>/dev/null)
        if [ ! -z "$PIDS" ]; then
            # Kill all processes using the port and their parent/child processes
            for PID in $PIDS; do
                echo "Stopping process $PID..."
                # Try graceful kill first (including parent process if it's uv)
                kill $PID 2>/dev/null
                # Also try to kill parent process if it's uv run python
                PARENT_PID=$(ps -o ppid= -p $PID 2>/dev/null | xargs)
                if [ ! -z "$PARENT_PID" ]; then
                    PARENT_CMD=$(ps -p $PARENT_PID -o cmd= 2>/dev/null)
                    if echo "$PARENT_CMD" | grep -q "uv run python\|uvicorn"; then
                        echo "Stopping parent process $PARENT_PID..."
                        kill $PARENT_PID 2>/dev/null
                    fi
                fi
                sleep 1
                # If still running, force kill
                if ps -p $PID > /dev/null 2>&1; then
                    echo "Force killing process $PID..."
                    kill -9 $PID 2>/dev/null
                fi
                if [ ! -z "$PARENT_PID" ] && ps -p $PARENT_PID > /dev/null 2>&1; then
                    echo "Force killing parent process $PARENT_PID..."
                    kill -9 $PARENT_PID 2>/dev/null
                fi
            done
            echo "Service stopped successfully (PIDs: $PIDS)"
            # Clean up PID file if exists
            [ -f "arena.pid" ] && rm arena.pid
            # Wait a moment for port to be released
            sleep 2
        else
            echo "No service running on port 8802"
            # Clean up stale PID file
            [ -f "arena.pid" ] && rm arena.pid
        fi
    else
        # Fallback: try to kill by PID file
        if [ -f "arena.pid" ]; then
            PID=$(cat arena.pid 2>/dev/null)
            if [ ! -z "$PID" ] && ps -p $PID > /dev/null 2>&1; then
                echo "Stopping process from PID file: $PID"
                kill $PID 2>/dev/null
                sleep 1
                if ps -p $PID > /dev/null 2>&1; then
                    kill -9 $PID 2>/dev/null
                fi
                echo "Service stopped successfully (PID: $PID)"
                rm arena.pid
            else
                echo "No running process found for PID in arena.pid"
                rm arena.pid
            fi
        else
            echo "lsof not available and no PID file found, cannot stop service"
        fi
    fi


    exit 0
fi

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo "=== Alpha Arena Startup Script ==="
echo "Project directory: $SCRIPT_DIR"
echo "Backend directory: $BACKEND_DIR"
echo "Frontend directory: $FRONTEND_DIR"

# Check if directories exist
if [ ! -d "$BACKEND_DIR" ]; then
    echo "ERROR: Backend directory not found at $BACKEND_DIR"
    exit 1
fi

if [ ! -d "$FRONTEND_DIR" ]; then
    echo "ERROR: Frontend directory not found at $FRONTEND_DIR"
    exit 1
fi

# Function to check and install Node.js
install_nodejs() {
    if ! command -v node &> /dev/null; then
        echo "Installing Node.js..."
        # Use NodeSource repository for latest LTS version
        curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
        sudo apt-get install -y nodejs
        
        if ! command -v node &> /dev/null; then
            echo "ERROR: Failed to install Node.js"
            exit 1
        fi
        echo "Node.js installed successfully: $(node --version)"
        echo "npm installed: $(npm --version)"
    else
        echo "Node.js already installed: $(node --version)"
    fi
}

# Function to check and install pnpm
install_pnpm() {
    if ! command -v pnpm &> /dev/null; then
        echo "Installing pnpm..."
        # Try official installer first (installs to user directory, no sudo needed)
        echo "Installing pnpm via official installer..."
        curl -fsSL https://get.pnpm.io/install.sh | sh -
        
        # Update PATH for current session
        if [ -d "$HOME/.local/share/pnpm" ]; then
            export PATH="$HOME/.local/share/pnpm:$PATH"
        fi
        
        # If still not found, try npm with sudo as fallback
        if ! command -v pnpm &> /dev/null; then
            if command -v npm &> /dev/null; then
                echo "Official installer failed, trying npm install with sudo..."
                sudo npm install -g pnpm
            fi
        fi

        if ! command -v pnpm &> /dev/null; then
            echo "ERROR: Failed to install pnpm"
            exit 1
        fi
        echo "pnpm installed successfully: $(pnpm --version)"
    else
        echo "pnpm already installed: $(pnpm --version)"
    fi
}

# Function to build frontend
build_frontend() {
    echo "Building frontend..."
    cd "$FRONTEND_DIR"

    # Ensure PATH includes pnpm
    if [ -d "$HOME/.local/share/pnpm" ]; then
        export PATH="$HOME/.local/share/pnpm:$PATH"
    fi

    # Always install/update frontend dependencies
    echo "Installing frontend dependencies..."
    pnpm install

    # Build frontend
    pnpm build
    if [ $? -ne 0 ]; then
        echo "ERROR: Frontend build failed"
        exit 1
    fi

    # Copy to backend static directory
    echo "Copying frontend build to backend/static..."
    mkdir -p "$BACKEND_DIR/static"
    rm -rf "$BACKEND_DIR/static"/*
    cp -r dist/* "$BACKEND_DIR/static/"

    echo "Frontend built and deployed successfully"
    cd "$BACKEND_DIR"
}

# Install Node.js if needed
install_nodejs

# Update PATH after Node.js installation
export PATH="/usr/bin:$PATH"

# Install pnpm if needed
install_pnpm

# Ensure PATH includes pnpm (refresh after installation)
if [ -d "$HOME/.local/share/pnpm" ]; then
    export PATH="$HOME/.local/share/pnpm:$PATH"
fi
# Also check global npm location
if [ -d "/usr/lib/node_modules/.bin" ]; then
    export PATH="/usr/lib/node_modules/.bin:$PATH"
fi

# Build frontend
build_frontend

echo "=== Alpha Arena Startup Script ==="
echo "Starting backend service on port 8802..."

# Check if port 8802 is already in use
if command -v lsof &> /dev/null; then
    PID=$(lsof -t -i:8802 2>/dev/null)
    if [ ! -z "$PID" ]; then
        echo "Port 8802 is already in use by process $PID"
        echo "Please stop the existing service first: ./start_arena.sh stop"
        exit 1
    fi
fi

# Change to backend directory
cd "$BACKEND_DIR"

# Check if uv is available, use it to sync dependencies
if command -v uv &> /dev/null; then
    echo "Syncing dependencies with uv..."
    uv sync
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to sync dependencies with uv"
        exit 1
    fi
    # Use uv to run the service
    PYTHON_CMD="uv run python"
else
    # Fallback to traditional venv approach
    echo "uv not found, using traditional venv approach..."
    # Check if virtual environment exists, create if not
    if [ ! -f ".venv/bin/python" ]; then
        echo "Creating Python virtual environment..."
        python3 -m venv .venv
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to create virtual environment"
            exit 1
        fi

        echo "Installing Python dependencies..."
        .venv/bin/pip install -e .
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to install Python dependencies"
            exit 1
        fi
        echo "Python environment setup completed"
    fi

    # Check if uvicorn is available in virtual environment
    if ! .venv/bin/python -c "import uvicorn" 2>/dev/null; then
        echo "ERROR: uvicorn not found in virtual environment."
        echo "Installing required dependencies..."
        .venv/bin/pip install -e .
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to install dependencies."
            exit 1
        fi
    fi
    PYTHON_CMD=".venv/bin/python"
fi

# Limit worker processes for safety (default: 2 workers)
# This prevents excessive resource usage while maintaining reasonable performance
MAX_WORKERS=${MAX_WORKERS:-2}
if [ "$MAX_WORKERS" -lt 1 ] || [ "$MAX_WORKERS" -gt 8 ]; then
    echo "Warning: MAX_WORKERS should be between 1 and 8. Using default: 1"
    MAX_WORKERS=1
fi

echo "Starting service with $MAX_WORKERS worker process(es)..."

# Start service in background with worker limit
nohup $PYTHON_CMD -m uvicorn main:app --host 0.0.0.0 --port 8802 --workers $MAX_WORKERS > ../arena.log 2>&1 &
echo $! > ../arena.pid

# Wait for service to start with retry logic
echo "Waiting for service to start..."
for i in {1..60}; do
    if curl -s http://127.0.0.1:8802/api/health > /dev/null 2>&1; then
        break
    fi
    echo -n "."
    sleep 2
done
echo ""

# Check if service is running
if curl -s http://127.0.0.1:8802/api/health > /dev/null 2>&1; then
    echo "✅ Service started successfully!"
    echo "   - Backend API: http://localhost:8802"
    echo "   - Health Check: http://localhost:8802/api/health"
    echo "   - System Logs API: http://localhost:8802/api/system-logs"
    echo ""
    echo "📊 System Log Features:"
    echo "   - View logs: GET /api/system-logs"
    echo "   - Get stats: GET /api/system-logs/stats"
    echo "   - Clear logs: DELETE /api/system-logs"
    echo ""
    echo "View live logs: tail -f arena.log"
    echo "Stop service: ./start_arena.sh stop"
else
    echo "❌ Service failed to start. Check logs:"
    echo "   tail -f arena.log"
fi

echo ""
echo "Database changes applied:"
echo "✅ Added prompt_snapshot column to ai_decision_logs"
echo "✅ Added reasoning_snapshot column to ai_decision_logs"
echo "✅ Added decision_snapshot column to ai_decision_logs"
