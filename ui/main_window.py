"""
Main window UI component for the Audio Recorder application.
"""
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
import threading
import logging
import pyperclip
import keyboard
from typing import Optional, Callable, Dict
from concurrent.futures import ThreadPoolExecutor
import os

from config import config
from recorder import AudioRecorder
from transcriber import TranscriptionBackend, LocalWhisperBackend
from hotkey_manager import HotkeyManager
from settings import settings_manager
from audio_processor import audio_processor
from .tray import TrayManager
from .waveform_overlay import WaveformOverlay
from .waveform_style_dialog import WaveformStyleDialog


class UIStatusController:
    """Manages UI status updates and overlay display."""
    
    def __init__(self, main_window):
        """Initialize the status controller.
        
        Args:
            main_window: Reference to the main window instance.
        """
        self.main_window = main_window
        # Initialize overlay with saved style if available
        try:
            from settings import settings_manager
            current_style, all_configs = settings_manager.load_waveform_style_settings()
            self.waveform_overlay = WaveformOverlay(main_window.root, initial_style=current_style)
            # Apply config explicitly in case style has non-defaults
            if current_style in all_configs:
                self.waveform_overlay.set_style(current_style, all_configs[current_style])
        except Exception:
            # Fallback to default overlay
            self.waveform_overlay = WaveformOverlay(main_window.root)
    
    def update_status(self, status: str, show_overlay: bool = True):
        """Update status in both main window and overlay.
        
        Args:
            status: Status message to display.
            show_overlay: Whether to show the overlay.
        """
        # Update main window status
        if self.main_window.status_label:
            self.main_window.status_label.config(text=f"Status: {status}")
        
        # Update waveform overlay if requested
        if show_overlay:
            # Map status messages to overlay states
            if "Recording" in status:
                self.waveform_overlay.show("recording", status)
            elif "Processing" in status:
                self.waveform_overlay.show("processing", status)
            elif "Transcribing" in status:
                self.waveform_overlay.show("transcribing", status)
            else:
                # For other statuses, hide the overlay
                self.waveform_overlay.hide()
        else:
            # Hide overlay when show_overlay is False
            self.waveform_overlay.hide()
    
    def clear_status(self):
        """Clear the status overlay."""
        self.waveform_overlay.hide()
    
    def update_status_with_auto_clear(self, status: str, delay_ms: int = None):
        """Update status with automatic clearing after a delay.
        
        Args:
            status: Status message to display.
            delay_ms: Delay in milliseconds before clearing. Uses config default if None.
        """
        from config import config
        delay = delay_ms or config.OVERLAY_HIDE_DELAY_MS
        
        # Special handling for STT enable/disable messages
        if "STT Enabled" in status:
            self._show_stt_status(status, "enabled")
        elif "STT Disabled" in status:
            self._show_stt_status(status, "disabled")
        else:
            # Update status and show overlay for other messages
            self.update_status(status, show_overlay=True)
        
        # Schedule clearing after delay
        self.main_window.root.after(delay, self.clear_status)
    
    def _show_stt_status(self, message: str, state: str):
        """Show STT status with specialized overlay state.

        Args:
            message: Status message to display.
            state: STT state ('enabled' or 'disabled').
        """
        # Update main window status
        if self.main_window.status_label:
            self.main_window.status_label.config(text=f"Status: {message}")

        # Show waveform overlay with appropriate state
        if self.waveform_overlay:
            if state == "enabled":
                # Show as a processing-like state with green-ish theme
                self.waveform_overlay.show("processing", message)
            else:
                # Show as specialized STT disable state with animation
                self.waveform_overlay.show("stt_disable", message)


class MainWindow:
    """Main application window with GUI controls and coordination logic."""
    
    def __init__(self):
        """Initialize the main window."""
        self.root = tk.Tk()
        self.root.title("B.L.A.D.E. - Brister's Linguistic Audio Dictation Engine")
        self.root.geometry("480x450")
        self.root.withdraw()  # Hide initially
        self.root.configure(bg=config.WAVEFORM_BG_COLOR)
        
        # Initialize components
        self.recorder = AudioRecorder()
        self.transcription_backends: Dict[str, TranscriptionBackend] = {}
        self.current_backend: Optional[TranscriptionBackend] = None
        self.hotkey_manager: Optional[HotkeyManager] = None
        self.tray_manager = TrayManager()
        self.executor = ThreadPoolExecutor(max_workers=2)
        
        # UI components
        self.status_label: Optional[tk.Label] = None
        self.transcription_text: Optional[tk.Text] = None
        self.start_button: Optional[ttk.Button] = None
        self.stop_button: Optional[ttk.Button] = None
        self.cancel_button: Optional[ttk.Button] = None
        self.open_file_button: Optional[ttk.Button] = None
        
        # Status management
        self.status_controller = UIStatusController(self)
        
        # Create overlay window
        self.overlay = tk.Toplevel(self.root)
        self.overlay.title("")
        self.overlay.geometry(config.OVERLAY_SIZE)
        self.overlay.attributes('-topmost', True)
        self.overlay.overrideredirect(True)
        self.overlay.withdraw()
        
        self.overlay_label = tk.Label(self.overlay, text="", bg='black', fg='white', pady=5)
        self.overlay_label.pack(fill=tk.BOTH, expand=True)
        
        # Setup components
        self._setup_transcription_backends()
        self._setup_gui()
        self._setup_hotkeys()
        self._setup_tray()
        self._setup_audio_level_callback()
        
        # Handle window close event
        self.root.protocol('WM_DELETE_WINDOW', self.on_closing)
        
        logging.info("Main window initialized")
    
    def _setup_transcription_backends(self):
        """Initialize transcription backend (local whisper only)."""
        # Always use Local Whisper backend
        self.transcription_backends['local_whisper'] = LocalWhisperBackend()
        self.current_backend = self.transcription_backends['local_whisper']
    
    def _setup_gui(self):
        """Create and configure the main GUI interface."""
        # Create menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # Create File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open Audio File...", command=self.open_audio_file)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit_app)
        
        # Create Settings menu
        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(label="Configure Hotkeys", command=self.open_hotkey_settings)
        settings_menu.add_command(label="Waveform Style...", command=self.open_waveform_style_settings)
        settings_menu.add_command(label="Configure FFmpeg...", command=self.configure_ffmpeg)
        settings_menu.add_command(label="Whisper Models...", command=self.open_whisper_models)
        

        # Main frame with padding - use tk.Frame for custom background
        main_frame = tk.Frame(self.root, bg=config.WAVEFORM_BG_COLOR, padx=20, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header with B.L.A.D.E. branding - Modern elevated design
        header_frame = tk.Frame(main_frame, bg=config.WAVEFORM_BG_COLOR)
        header_frame.pack(fill=tk.X, pady=(5, 20))

        # Main title with dramatic size
        title_label = tk.Label(
            header_frame,
            text="B.L.A.D.E.",
            font=("Segoe UI", 42, "bold"),
            bg=config.WAVEFORM_BG_COLOR,
            fg=config.WAVEFORM_ACCENT_COLOR,
            pady=8
        )
        title_label.pack()

        # Subtitle with increased prominence and elegant spacing
        subtitle_label = tk.Label(
            header_frame,
            text="Brister's Linguistic Audio Dictation Engine",
            font=("Segoe UI", 13, "normal"),
            bg=config.WAVEFORM_BG_COLOR,
            fg="#b0b0b0",
            pady=5
        )
        subtitle_label.pack()

        # Modern gradient-style separator (dual line effect)
        separator_container = tk.Frame(main_frame, bg=config.WAVEFORM_BG_COLOR)
        separator_container.pack(fill=tk.X, pady=(0, 18))

        separator_top = tk.Frame(separator_container, height=1, bg=config.WAVEFORM_ACCENT_COLOR)
        separator_top.pack(fill=tk.X)

        tk.Frame(separator_container, height=2, bg=config.WAVEFORM_BG_COLOR).pack()

        separator_bottom = tk.Frame(separator_container, height=1, bg=config.WAVEFORM_SECONDARY_COLOR)
        separator_bottom.pack(fill=tk.X)

        # Status label with elevated modern styling and subtle border
        status_outer = tk.Frame(main_frame, bg=config.WAVEFORM_SECONDARY_COLOR, bd=0)
        status_outer.pack(fill=tk.X, pady=(0, 18))

        status_frame = tk.Frame(status_outer, bg="#2a2a2a", bd=0)
        status_frame.pack(fill=tk.BOTH, padx=1, pady=1)

        self.status_label = tk.Label(
            status_frame,
            text="Status: Ready",
            font=("Segoe UI", 11),
            bg="#2a2a2a",
            fg=config.WAVEFORM_TEXT_COLOR,
            pady=10,
            padx=12
        )
        self.status_label.pack(fill=tk.X)

        # Recording buttons frame
        recording_frame = tk.Frame(main_frame, bg=config.WAVEFORM_BG_COLOR)
        recording_frame.pack(pady=(0, 10), fill=tk.X)

        # Configure custom button style
        style = ttk.Style()
        style.theme_use('clam')

        # Start button style (accent color) - Enhanced modern look
        style.configure('Start.TButton',
                       background=config.WAVEFORM_ACCENT_COLOR,
                       foreground='white',
                       borderwidth=0,
                       focuscolor='none',
                       font=('Segoe UI', 11, 'bold'),
                       padding=(12, 10))
        style.map('Start.TButton',
                 background=[('active', config.WAVEFORM_SECONDARY_COLOR)])

        # Stop button style (warning color) - Enhanced modern look
        style.configure('Stop.TButton',
                       background='#ff6b6b',
                       foreground='white',
                       borderwidth=0,
                       focuscolor='none',
                       font=('Segoe UI', 11, 'bold'),
                       padding=(12, 10))
        style.map('Stop.TButton',
                 background=[('active', '#ff5252')])

        # Cancel button style - Modern sizing
        style.configure('Cancel.TButton',
                       background='#444444',
                       foreground='white',
                       borderwidth=0,
                       focuscolor='none',
                       font=('Segoe UI', 10),
                       padding=(10, 8))
        style.map('Cancel.TButton',
                 background=[('active', '#555555')])

        # File button style - Enhanced modern look
        style.configure('File.TButton',
                       background=config.WAVEFORM_SECONDARY_COLOR,
                       foreground='white',
                       borderwidth=0,
                       focuscolor='none',
                       font=('Segoe UI', 11),
                       padding=(12, 10))
        style.map('File.TButton',
                 background=[('active', '#007aa3')])

        self.start_button = ttk.Button(
            recording_frame,
            text="🎤 Start Recording",
            command=self.start_recording,
            style='Start.TButton'
        )
        self.start_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.stop_button = ttk.Button(
            recording_frame,
            text="⏹ Stop Recording",
            command=self.stop_recording,
            state=tk.DISABLED,
            style='Stop.TButton'
        )
        self.stop_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0))

        # Open file button
        self.open_file_button = ttk.Button(
            main_frame,
            text="📁 Open Audio File",
            command=self.open_audio_file,
            style='File.TButton'
        )
        self.open_file_button.pack(pady=(0, 10), fill=tk.X)

        # Cancel button
        self.cancel_button = ttk.Button(
            main_frame,
            text="✖ Cancel",
            command=self.cancel_transcription,
            state=tk.DISABLED,
            style='Cancel.TButton'
        )
        self.cancel_button.pack(pady=(0, 15), fill=tk.X)

        # Transcription display section with modern styling
        transcription_label = tk.Label(
            main_frame,
            text="Transcription",
            font=("Segoe UI", 11, "bold"),
            bg=config.WAVEFORM_BG_COLOR,
            fg=config.WAVEFORM_ACCENT_COLOR,
            anchor='w'
        )
        transcription_label.pack(fill=tk.X, pady=(0, 8))

        # Scrollable text frame with subtle border
        text_outer = tk.Frame(main_frame, bg=config.WAVEFORM_SECONDARY_COLOR, bd=0)
        text_outer.pack(fill=tk.BOTH, expand=True)

        text_frame = tk.Frame(text_outer, bg="#2a2a2a", bd=0)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Add scrollbar
        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.transcription_text = tk.Text(
            text_frame,
            height=5,
            wrap=tk.WORD,
            relief=tk.FLAT,
            font=('Segoe UI', 10),
            bg="#2a2a2a",
            fg=config.WAVEFORM_TEXT_COLOR,
            insertbackground=config.WAVEFORM_ACCENT_COLOR,
            padx=12,
            pady=12,
            yscrollcommand=scrollbar.set
        )
        self.transcription_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.transcription_text.yview)
        self.transcription_text.config(state=tk.DISABLED)
    
    def _setup_hotkeys(self):
        """Setup hotkey management."""
        hotkeys = settings_manager.load_hotkey_settings()
        self.hotkey_manager = HotkeyManager(hotkeys)
        self.hotkey_manager.set_callbacks(
            on_record_toggle=self.toggle_recording,
            on_cancel=self.cancel_transcription,
            on_status_update=self.status_controller.update_status_with_auto_clear,
            on_status_update_auto_hide=self.status_controller.update_status_with_auto_clear
        )
    
    def _setup_tray(self):
        """Setup system tray."""
        self.tray_manager.set_callbacks(
            on_show=self.show_window,
            on_quit=self.quit_app
        )
    
    def _setup_audio_level_callback(self):
        """Setup audio level callback for waveform overlay."""
        def audio_level_callback(level: float):
            # Update waveform overlay with audio level
            if self.status_controller.waveform_overlay:
                self.status_controller.waveform_overlay.update_audio_level(level)
        
        # Set the callback on the audio recorder
        self.recorder.set_audio_level_callback(audio_level_callback)
    
    def show_window(self):
        """Show the main window."""
        self.root.deiconify()
        self.root.state('normal')
        logging.info("Main window shown")
    
    def hide_window(self):
        """Hide the main window."""
        self.root.withdraw()
        self.tray_manager.show_tray()
        logging.info("Main window hidden")
    
    def on_closing(self):
        """Handle window closing event."""
        self.hide_window()
    
    def start_recording(self):
        """Start audio recording."""
        if self.recorder.start_recording():
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            self.cancel_button.config(state=tk.NORMAL)
            self.open_file_button.config(state=tk.DISABLED)
            self.status_controller.update_status("Recording...")
            logging.info("Recording started from GUI")
    
    def stop_recording(self):
        """Stop audio recording and start transcription."""
        # Update UI immediately for instant response
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        # Show transcribing status immediately for better UX
        self.status_controller.update_status("Transcribing...")
        
        if self.recorder.stop_recording():
            # Check if we have actual recording data
            if not self.recorder.has_recording_data():
                logging.error("No recording data available after stopping")
                self._on_transcription_error("No audio data recorded")
                return
            
            # Save recording
            if not self.recorder.save_recording():
                logging.error("Failed to save recording")
                self._on_transcription_error("Failed to save audio file")
                return
            
            # Verify the audio file exists and has content
            import os
            if not os.path.exists(config.RECORDED_AUDIO_FILE):
                logging.error(f"Audio file not found: {config.RECORDED_AUDIO_FILE}")
                self._on_transcription_error("Audio file not created")
                return
            
            file_size = os.path.getsize(config.RECORDED_AUDIO_FILE)
            logging.info(f"Audio file size: {file_size} bytes")
            if file_size < 100:  # WAV header is about 44 bytes, so anything less than 100 is suspect
                logging.error(f"Audio file too small: {file_size} bytes")
                self._on_transcription_error("Audio file is empty or corrupted")
                return
            
            # Check if file needs splitting due to size limit
            try:
                needs_splitting, file_size_mb = audio_processor.check_file_size(config.RECORDED_AUDIO_FILE)
                
                if needs_splitting:
                    logging.info(f"Large file detected ({file_size_mb:.2f} MB), starting split workflow")
                    # Update status to indicate large file processing
                    self.status_controller.update_status(f"Processing large file ({file_size_mb:.1f} MB)...")
                    # Start split and transcribe workflow
                    future = self.executor.submit(self._transcribe_large_audio)
                else:
                    # Normal transcription workflow
                    future = self.executor.submit(self._transcribe_audio)
                    
                logging.info(f"Recording stopped, transcription started. Audio duration: {self.recorder.get_recording_duration():.2f}s")
                
            except Exception as e:
                logging.error(f"Failed to check file size: {e}")
                self._on_transcription_error(f"Failed to process audio file: {e}")
                return
    
    def toggle_recording(self):
        """Toggle between starting and stopping recording."""
        logging.info(f"Toggle recording called. Current state: is_recording={self.recorder.is_recording}")
        if not self.recorder.is_recording:
            self.start_recording()
        else:
            self.stop_recording()
    
    def cancel_transcription(self):
        """Cancel recording or transcription."""
        logging.info(f"Cancel called. Recording: {self.recorder.is_recording}, Transcribing: {self.current_backend and self.current_backend.is_transcribing}")

        if self.recorder.is_recording:
            self.recorder.stop_recording()
            # Clear the recording data since it was cancelled
            self.recorder.clear_recording_data()
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            self.open_file_button.config(state=tk.NORMAL)
            # Show cancelling animation
            self.status_controller.waveform_overlay.show_canceling("Recording Cancelled")
            logging.info("Recording cancelled and data cleared")

        elif self.current_backend and self.current_backend.is_transcribing:
            self.current_backend.cancel_transcription()
            self.start_button.config(state=tk.NORMAL)
            self.open_file_button.config(state=tk.NORMAL)
            # Show cancelling animation
            self.status_controller.waveform_overlay.show_canceling("Transcription Cancelled")
            logging.info("Transcription cancelled")

        else:
            # If neither recording nor transcribing, still show a brief cancellation message
            self.status_controller.waveform_overlay.show_canceling("Cancelled")
            self.start_button.config(state=tk.NORMAL)
            self.open_file_button.config(state=tk.NORMAL)

        self.cancel_button.config(state=tk.DISABLED)
    
    def _transcribe_audio(self):
        """Transcribe audio in background thread."""
        try:
            self.status_controller.update_status("Transcribing...")
            
            # Transcribe using current backend
            transcribed_text = self.current_backend.transcribe(config.RECORDED_AUDIO_FILE)
            
            # Update UI on main thread
            self.root.after(0, self._on_transcription_complete, transcribed_text)
            
        except Exception as e:
            logging.error(f"Transcription failed: {e}")
            self.root.after(0, self._on_transcription_error, str(e))
    
    def _transcribe_large_audio(self):
        """Transcribe large audio file by splitting it into chunks."""
        chunk_files = []
        try:
            # Add status update immediately when large audio processing starts
            self.root.after(0, lambda: self.status_controller.update_status("Processing large audio file..."))
            
            # Step 1: Split the audio file
            def progress_callback(message):
                """Update status with splitting progress."""
                self.root.after(0, lambda: self.status_controller.update_status(message))
            
            chunk_files = audio_processor.split_audio_file(config.RECORDED_AUDIO_FILE, progress_callback)
            
            if not chunk_files:
                raise Exception("Failed to split audio file into chunks")
            
            # Step 2: Check if backend supports chunked transcription
            if hasattr(self.current_backend, 'transcribe_chunks'):
                # Use backend's chunked transcription if available
                self.root.after(0, lambda: self.status_controller.update_status(f"Transcribing {len(chunk_files)} chunks..."))
                transcribed_text = self.current_backend.transcribe_chunks(chunk_files)
            else:
                # Fallback: transcribe chunks individually and combine
                transcriptions = []
                for i, chunk_file in enumerate(chunk_files):
                    # Create a proper closure for the lambda
                    current_chunk = i + 1
                    total_chunks = len(chunk_files)
                    self.root.after(0, lambda: self.status_controller.update_status(f"Transcribing chunk {current_chunk}/{total_chunks}..."))
                    
                    chunk_transcription = self.current_backend.transcribe(chunk_file)
                    transcriptions.append(chunk_transcription)
                    
                    logging.info(f"Completed chunk {current_chunk}/{total_chunks}")
                
                # Combine transcriptions
                self.root.after(0, lambda: self.status_controller.update_status("Combining transcriptions..."))
                transcribed_text = audio_processor.combine_transcriptions(transcriptions)
            
            # Update UI on main thread
            self.root.after(0, self._on_transcription_complete, transcribed_text)
            
        except Exception as e:
            logging.error(f"Large audio transcription failed: {e}")
            self.root.after(0, self._on_transcription_error, str(e))
        finally:
            # Cleanup temporary chunk files
            try:
                audio_processor.cleanup_temp_files()
            except Exception as cleanup_error:
                logging.warning(f"Failed to cleanup temp files: {cleanup_error}")
    
    def _on_transcription_complete(self, transcribed_text: str):
        """Handle transcription completion on main thread."""
        # Update transcription display
        self.transcription_text.config(state=tk.NORMAL)
        self.transcription_text.delete(1.0, tk.END)
        self.transcription_text.insert(tk.END, f"Transcription: {transcribed_text}")
        self.transcription_text.config(state=tk.DISABLED)

        # Auto-paste the transcription
        self._paste_text(transcribed_text)

        # Clear the overlay and update status
        self.status_controller.clear_status()
        self.status_controller.update_status("Ready (Pasted)", show_overlay=False)
        self.cancel_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.NORMAL)
        self.open_file_button.config(state=tk.NORMAL)

        logging.info("Transcription completed and pasted")
    
    def _on_transcription_error(self, error_message: str):
        """Handle transcription error on main thread."""
        # Clear the overlay first
        self.status_controller.clear_status()

        messagebox.showerror("Error", f"Transcription failed: {error_message}")
        self.status_controller.update_status("Ready", show_overlay=False)
        self.cancel_button.config(state=tk.DISABLED)
        self.start_button.config(state=tk.NORMAL)
        self.open_file_button.config(state=tk.NORMAL)
    
    def _paste_text(self, text: str):
        """Paste text at current cursor position."""
        pyperclip.copy(text)
        keyboard.send('ctrl+v')

    def show_status_overlay(self, message: str):
        """Show status overlay with message."""
        if message:
            # Position overlay near mouse cursor
            x = self.root.winfo_pointerx() + 10
            y = self.root.winfo_pointery() + 10
            self.overlay.geometry(f"+{x}+{y}")
            
            self.overlay_label.config(text=message)
            self.overlay.deiconify()
        else:
            self.overlay.withdraw()
    
    def open_hotkey_settings(self):
        """Open hotkey configuration dialog."""
        from .hotkey_dialog import HotkeyDialog
        dialog = HotkeyDialog(self.root, self.hotkey_manager)
        dialog.show()
    
    def open_waveform_style_settings(self):
        """Open waveform style configuration dialog."""
        try:
            # Load current style via SettingsManager
            current_style, all_configs = settings_manager.load_waveform_style_settings()
            current_config = all_configs.get(current_style, {})

            # Create and show dialog (modal)
            dialog = WaveformStyleDialog(self.root, current_style, current_config)
            dialog.show()
            if getattr(dialog, 'dialog', None) is not None:
                # Wait for dialog to close to ensure settings are saved
                try:
                    dialog.dialog.wait_window(dialog.dialog)
                except Exception:
                    pass

            # After dialog closes, reload settings and apply to overlay immediately
            new_style, new_configs = settings_manager.load_waveform_style_settings()
            new_config = new_configs.get(new_style, {})
            if self.status_controller.waveform_overlay:
                self.status_controller.waveform_overlay.set_style(new_style, new_config)
            
        except Exception as e:
            logging.error(f"Failed to open waveform style settings: {e}")
            messagebox.showerror("Error", f"Failed to open waveform style settings: {e}")
    
    def configure_ffmpeg(self):
        """Open FFmpeg configuration dialog."""
        try:
            from .ffmpeg_dialog import FFmpegConfigDialog
            dialog = FFmpegConfigDialog(self.root)
            dialog.show_config_dialog()
        except Exception as e:
            logging.error(f"Failed to open FFmpeg dialog: {e}")
            messagebox.showerror("Error", f"Failed to open FFmpeg configuration: {e}")

    def open_whisper_models(self):
        """Open Whisper model management dialog."""
        try:
            from .whisper_model_dialog import WhisperModelDialog
            # Pass the local whisper backend so models can be reloaded without restart
            backend = self.transcription_backends.get('local_whisper')
            dialog = WhisperModelDialog(self.root, backend)
            dialog.show()
        except Exception as e:
            logging.error(f"Failed to open Whisper model dialog: {e}")
            messagebox.showerror("Error", f"Failed to open Whisper model settings: {e}")

    def open_audio_file(self):
        """Open an existing audio file for transcription."""
        # Define supported file types
        filetypes = (
            ('Audio Files', '*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma'),
            ('WAV files', '*.wav'),
            ('MP3 files', '*.mp3'),
            ('M4A files', '*.m4a'),
            ('FLAC files', '*.flac'),
            ('OGG files', '*.ogg'),
            ('All files', '*.*')
        )

        # Open file dialog
        filename = filedialog.askopenfilename(
            title='Open Audio File',
            initialdir=os.path.expanduser('~'),
            filetypes=filetypes
        )

        if not filename:
            # User cancelled
            return

        # Verify file exists
        if not os.path.exists(filename):
            messagebox.showerror("Error", f"File not found: {filename}")
            return

        # Check file size
        file_size = os.path.getsize(filename)
        if file_size < 100:
            messagebox.showerror("Error", "Audio file is too small or empty")
            return

        logging.info(f"Opening audio file: {filename} ({file_size} bytes)")

        # Disable buttons during transcription
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.open_file_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)

        # Update status
        self.status_controller.update_status("Processing audio file...")

        # Start transcription in background
        try:
            needs_splitting, file_size_mb = audio_processor.check_file_size(filename)

            if needs_splitting:
                logging.info(f"Large file detected ({file_size_mb:.2f} MB), starting split workflow")
                self.status_controller.update_status(f"Processing large file ({file_size_mb:.1f} MB)...")
                future = self.executor.submit(self._transcribe_large_audio_from_file, filename)
            else:
                # Normal transcription workflow
                future = self.executor.submit(self._transcribe_audio_from_file, filename)

        except Exception as e:
            logging.error(f"Failed to process audio file: {e}")
            self._on_transcription_error(f"Failed to process audio file: {e}")
            self.open_file_button.config(state=tk.NORMAL)

    def _transcribe_audio_from_file(self, audio_file: str):
        """Transcribe audio from an existing file in background thread."""
        try:
            self.status_controller.update_status("Transcribing...")

            # Transcribe using current backend
            transcribed_text = self.current_backend.transcribe(audio_file)

            # Update UI on main thread
            self.root.after(0, self._on_transcription_complete, transcribed_text)

        except Exception as e:
            logging.error(f"Transcription failed: {e}")
            self.root.after(0, self._on_transcription_error, str(e))

    def _transcribe_large_audio_from_file(self, audio_file: str):
        """Transcribe large audio file by splitting it into chunks."""
        chunk_files = []
        try:
            # Add status update immediately when large audio processing starts
            self.root.after(0, lambda: self.status_controller.update_status("Processing large audio file..."))

            # Step 1: Split the audio file
            def progress_callback(message):
                """Update status with splitting progress."""
                self.root.after(0, lambda: self.status_controller.update_status(message))

            chunk_files = audio_processor.split_audio_file(audio_file, progress_callback)

            if not chunk_files:
                raise Exception("Failed to split audio file into chunks")

            # Step 2: Check if backend supports chunked transcription
            if hasattr(self.current_backend, 'transcribe_chunks'):
                # Use backend's chunked transcription if available
                self.root.after(0, lambda: self.status_controller.update_status(f"Transcribing {len(chunk_files)} chunks..."))
                transcribed_text = self.current_backend.transcribe_chunks(chunk_files)
            else:
                # Fallback: transcribe chunks individually and combine
                transcriptions = []
                for i, chunk_file in enumerate(chunk_files):
                    # Create a proper closure for the lambda
                    current_chunk = i + 1
                    total_chunks = len(chunk_files)
                    self.root.after(0, lambda: self.status_controller.update_status(f"Transcribing chunk {current_chunk}/{total_chunks}..."))

                    chunk_transcription = self.current_backend.transcribe(chunk_file)
                    transcriptions.append(chunk_transcription)

                    logging.info(f"Completed chunk {current_chunk}/{total_chunks}")

                # Combine transcriptions
                self.root.after(0, lambda: self.status_controller.update_status("Combining transcriptions..."))
                transcribed_text = audio_processor.combine_transcriptions(transcriptions)

            # Update UI on main thread
            self.root.after(0, self._on_transcription_complete, transcribed_text)

        except Exception as e:
            logging.error(f"Large audio transcription failed: {e}")
            self.root.after(0, self._on_transcription_error, str(e))
        finally:
            # Cleanup temporary chunk files
            try:
                audio_processor.cleanup_temp_files()
            except Exception as cleanup_error:
                logging.warning(f"Failed to cleanup temp files: {cleanup_error}")


    def quit_app(self):
        """Quit the application."""
        logging.info("Quitting application")
        self.cleanup()
        self.root.quit()
    
    def cleanup(self):
        """Clean up resources."""
        try:
            # Clear any remaining overlay
            self.status_controller.clear_status()
            
            # Cleanup waveform overlay
            if self.status_controller.waveform_overlay:
                self.status_controller.waveform_overlay.cleanup()
            
            if self.hotkey_manager:
                self.hotkey_manager.cleanup()
            
            if self.tray_manager:
                self.tray_manager.cleanup()
            
            if self.recorder:
                self.recorder.cleanup()
            
            if self.executor:
                self.executor.shutdown(wait=False)
                
            logging.info("Main window cleanup completed")
            
        except Exception as e:
            logging.error(f"Error during cleanup: {e}")
    
    def run(self):
        """Start the main application loop."""
        self.root.attributes('-topmost', True)
        self.root.deiconify()  # Show window
        
        try:
            self.root.mainloop()
        finally:
            self.cleanup()
