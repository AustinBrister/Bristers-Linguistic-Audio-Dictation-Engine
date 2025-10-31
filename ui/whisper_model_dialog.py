"""
Whisper model management dialog.
"""
import tkinter as tk
from tkinter import ttk, messagebox
import os
import threading
import logging
from pathlib import Path
from settings import settings_manager
from config import config


class WhisperModelDialog:
    """Dialog for managing Whisper models."""

    # Model information: name, size, description
    # Using English-only models (.en) for better performance and accuracy
    MODELS = {
        'tiny.en': {
            'size': '~75 MB',
            'description': 'Fastest, least accurate. Good for quick drafts. English-only.',
            'speed': 'Very Fast'
        },
        'base.en': {
            'size': '~145 MB',
            'description': 'Balanced speed and accuracy. Recommended for most users. English-only.',
            'speed': 'Fast',
            'recommended': True
        },
        'small.en': {
            'size': '~470 MB',
            'description': 'Better accuracy, slower processing. English-only.',
            'speed': 'Moderate'
        },
        'medium.en': {
            'size': '~1.5 GB',
            'description': 'High accuracy, significantly slower. English-only.',
            'speed': 'Slow'
        },
        'large-v2': {
            'size': '~3 GB',
            'description': 'Best accuracy, very slow processing. Multi-language (no .en variant available).',
            'speed': 'Very Slow'
        }
    }

    def __init__(self, parent=None, backend=None):
        """Initialize the Whisper model dialog.

        Args:
            parent: Parent window.
            backend: LocalWhisperBackend instance for live model reloading.
        """
        self.parent = parent
        self.backend = backend
        self.dialog = None
        self.model_vars = {}

        # Load current model from settings
        settings = settings_manager.load_all_settings()
        self.current_model = settings.get('whisper_model', config.DEFAULT_WHISPER_MODEL)
        self.selected_model = None  # Will be created after dialog window exists
        self.downloading = False
        self.download_cancelled = False
        self.current_download_model = None

        # Get cache directory
        self.cache_dir = self._get_whisper_cache_dir()

    def _get_whisper_cache_dir(self) -> Path:
        """Get the Whisper model cache directory."""
        # Check environment variable first
        cache_root = os.environ.get('XDG_CACHE_HOME')
        if cache_root:
            return Path(cache_root) / 'whisper'

        # Default to user's home directory
        home = Path.home()
        if os.name == 'nt':  # Windows
            return home / '.cache' / 'whisper'
        else:  # Linux/Mac
            return home / '.cache' / 'whisper'

    def _is_model_downloaded(self, model_name: str) -> bool:
        """Check if a model is already downloaded.

        Args:
            model_name: Name of the model to check.

        Returns:
            True if model is downloaded, False otherwise.
        """
        if not self.cache_dir.exists():
            return False

        # Check for .pt files matching the model name
        model_files = list(self.cache_dir.glob(f'{model_name}*.pt'))
        return len(model_files) > 0

    def show(self) -> bool:
        """Show the Whisper model management dialog.

        Returns:
            True if changes were made, False otherwise.
        """
        self.dialog = tk.Toplevel(self.parent) if self.parent else tk.Tk()
        self.dialog.title("Whisper Model Settings")
        self.dialog.geometry("600x650")
        self.dialog.resizable(False, False)
        self.dialog.configure(bg=config.WAVEFORM_BG_COLOR)

        # Create StringVar now that we have a dialog window
        self.selected_model = tk.StringVar(self.dialog, value=self.current_model)

        # Flag to prevent trace from firing during initialization
        self.initialized = False

        # Center the dialog
        if self.parent:
            self.dialog.transient(self.parent)
            self.dialog.grab_set()

        self._create_widgets()

        # Now that widgets are created, enable the trace callback
        self.initialized = True
        self.selected_model.trace('w', self._on_model_selected)

        # Wait for dialog to close
        if self.parent:
            self.dialog.wait_window()
        else:
            self.dialog.mainloop()

        return True

    def _create_widgets(self):
        """Create dialog widgets."""
        main_frame = tk.Frame(self.dialog, bg=config.WAVEFORM_BG_COLOR, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        header_label = tk.Label(
            main_frame,
            text="Whisper Model Settings",
            font=("Segoe UI", 16, "bold"),
            bg=config.WAVEFORM_BG_COLOR,
            fg=config.WAVEFORM_ACCENT_COLOR
        )
        header_label.pack(pady=(0, 5))

        # Description
        desc_label = tk.Label(
            main_frame,
            text="Manage and select Whisper models for local transcription",
            font=("Segoe UI", 10),
            bg=config.WAVEFORM_BG_COLOR,
            fg="#b0b0b0"
        )
        desc_label.pack(pady=(0, 15))

        # Separator
        separator = tk.Frame(main_frame, height=1, bg=config.WAVEFORM_SECONDARY_COLOR)
        separator.pack(fill=tk.X, pady=(0, 15))

        # Info about storage
        cache_info = tk.Label(
            main_frame,
            text=f"Models are stored in: {self.cache_dir}",
            font=("Segoe UI", 8),
            bg=config.WAVEFORM_BG_COLOR,
            fg="#808080",
            anchor='w'
        )
        cache_info.pack(fill=tk.X, pady=(0, 15))

        # Models list frame with scrollbar
        list_frame = tk.Frame(main_frame, bg="#2a2a2a", bd=0)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        # Create canvas for scrolling
        canvas = tk.Canvas(list_frame, bg="#2a2a2a", highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="#2a2a2a")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Build model list
        for model_name, info in self.MODELS.items():
            self._create_model_row(scrollable_frame, model_name, info)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Buttons frame - needs to be created early so we can add progress to it
        button_frame = tk.Frame(main_frame, bg=config.WAVEFORM_BG_COLOR)
        button_frame.pack(fill=tk.X, pady=(15, 0))

        # Download progress section inside button frame (initially hidden)
        self.progress_section = tk.Frame(button_frame, bg="#2a2a2a", bd=2, relief=tk.SOLID)

        self.progress_label = tk.Label(
            self.progress_section,
            text="Downloading model...",
            font=("Segoe UI", 11, "bold"),
            bg="#2a2a2a",
            fg=config.WAVEFORM_ACCENT_COLOR,
            anchor='w'
        )
        self.progress_label.pack(fill=tk.X, padx=15, pady=(15, 5))

        self.progress_status = tk.Label(
            self.progress_section,
            text="Downloading model, please wait...",
            font=("Segoe UI", 12),
            bg="#2a2a2a",
            fg="#b0b0b0",
            anchor='center',
            pady=20
        )
        self.progress_status.pack(fill=tk.X, padx=15, pady=(10, 15))

        # Separator
        tk.Frame(self.progress_section, bg=config.WAVEFORM_SECONDARY_COLOR, height=2).pack(fill=tk.X, padx=15, pady=(0, 15))

        # Cancel button
        self.cancel_download_btn = tk.Button(
            self.progress_section,
            text="✖  Cancel Download",
            font=("Segoe UI", 11, "bold"),
            bg="#cc0000",
            fg="white",
            activebackground="#990000",
            activeforeground="white",
            bd=0,
            relief=tk.RAISED,
            padx=30,
            pady=12,
            cursor="hand2",
            command=self._cancel_download
        )
        self.cancel_download_btn.pack(padx=15, pady=(0, 15))

        # Style for close button
        style = ttk.Style()

        style.configure('ModelDialogCancel.TButton',
                       background='#444444',
                       foreground='white',
                       borderwidth=0,
                       focuscolor='none',
                       font=('Segoe UI', 10),
                       padding=(12, 8))

        self.close_button = ttk.Button(
            button_frame,
            text="Close",
            command=self._close,
            style='ModelDialogCancel.TButton'
        )
        self.close_button.pack(fill=tk.X)

    def _create_model_row(self, parent, model_name: str, info: dict):
        """Create a row for a model.

        Args:
            parent: Parent widget.
            model_name: Name of the model.
            info: Model information dictionary.
        """
        is_downloaded = self._is_model_downloaded(model_name)
        is_recommended = info.get('recommended', False)

        # Model row frame
        row_frame = tk.Frame(parent, bg="#1a1a1a", bd=0)
        row_frame.pack(fill=tk.X, padx=8, pady=4)

        inner_frame = tk.Frame(row_frame, bg="#1a1a1a")
        inner_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # Left side - radio button and model info
        left_frame = tk.Frame(inner_frame, bg="#1a1a1a")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top row - name, recommended badge, downloaded checkmark
        top_row = tk.Frame(left_frame, bg="#1a1a1a")
        top_row.pack(fill=tk.X, pady=(0, 3))

        # Radio button
        radio = tk.Radiobutton(
            top_row,
            variable=self.selected_model,
            value=model_name,
            bg="#1a1a1a",
            fg=config.WAVEFORM_TEXT_COLOR,
            selectcolor="#2a2a2a",
            activebackground="#1a1a1a",
            activeforeground=config.WAVEFORM_ACCENT_COLOR,
            font=("Segoe UI", 11, "bold"),
            text=model_name.capitalize()
        )
        radio.pack(side=tk.LEFT)

        # Recommended badge
        if is_recommended:
            rec_label = tk.Label(
                top_row,
                text="⭐ RECOMMENDED",
                font=("Segoe UI", 8, "bold"),
                bg="#1a1a1a",
                fg=config.WAVEFORM_ACCENT_COLOR
            )
            rec_label.pack(side=tk.LEFT, padx=(8, 0))

        # Downloaded checkmark
        if is_downloaded:
            check_label = tk.Label(
                top_row,
                text="✓ Downloaded",
                font=("Segoe UI", 8),
                bg="#1a1a1a",
                fg="#4caf50"
            )
            check_label.pack(side=tk.LEFT, padx=(8, 0))

        # Description
        desc_label = tk.Label(
            left_frame,
            text=info['description'],
            font=("Segoe UI", 9),
            bg="#1a1a1a",
            fg="#b0b0b0",
            anchor='w',
            justify='left'
        )
        desc_label.pack(fill=tk.X, pady=(0, 2))

        # Size and speed info
        info_text = f"Size: {info['size']} • Speed: {info['speed']}"
        info_label = tk.Label(
            left_frame,
            text=info_text,
            font=("Segoe UI", 8),
            bg="#1a1a1a",
            fg="#808080",
            anchor='w'
        )
        info_label.pack(fill=tk.X)

        # Right side - download/delete button
        if not is_downloaded:
            download_btn = tk.Button(
                inner_frame,
                text="Download",
                font=("Segoe UI", 9),
                bg=config.WAVEFORM_SECONDARY_COLOR,
                fg="white",
                bd=0,
                padx=15,
                pady=6,
                cursor="hand2",
                command=lambda: self._download_model(model_name, download_btn)
            )
            download_btn.pack(side=tk.RIGHT)
        else:
            delete_btn = tk.Button(
                inner_frame,
                text="Delete",
                font=("Segoe UI", 9),
                bg="#cc0000",
                fg="white",
                bd=0,
                padx=15,
                pady=6,
                cursor="hand2",
                command=lambda: self._delete_model(model_name, row_frame)
            )
            delete_btn.pack(side=tk.RIGHT)

    def _download_model(self, model_name: str, button: tk.Button):
        """Download a Whisper model.

        Args:
            model_name: Name of the model to download.
            button: The download button to update.
        """
        if self.downloading:
            messagebox.showwarning("Download in Progress",
                                 "Another model is currently being downloaded. Please wait.")
            return

        model_info = self.MODELS[model_name]

        # Confirm download
        response = messagebox.askyesno(
            "Confirm Download",
            f"Download the '{model_name}' model?\n\n"
            f"Size: {model_info['size']}\n"
            f"Speed: {model_info['speed']}\n\n"
            f"The model will be downloaded from OpenAI's servers.\n"
            f"This may take several minutes depending on your internet connection."
        )

        if not response:
            return

        self.downloading = True
        self.download_cancelled = False
        self.current_download_model = model_name
        button.config(text="Downloading...", state=tk.DISABLED, bg="#666666")

        # Show progress section ABOVE the close button
        self.progress_label.config(text=f"Downloading '{model_name}' model ({model_info['size']})...")
        self.progress_section.pack(fill=tk.X, pady=(0, 15))

        # Disable Close button during download
        self.close_button.config(state=tk.DISABLED)

        # Force immediate UI update
        self.dialog.update()

        # Download in background thread
        def download_thread():
            try:
                import whisper
                # This will download the model if not present
                whisper.load_model(model_name)

                # Check if cancelled
                if not self.download_cancelled:
                    # Update UI on main thread
                    self.dialog.after(0, lambda: self._download_complete(model_name, button, True))
                else:
                    # Download completed but was cancelled - delete the files
                    self.dialog.after(0, lambda: self._cleanup_cancelled_download(model_name, button))
            except Exception as e:
                if not self.download_cancelled:
                    # Update UI on main thread
                    self.dialog.after(0, lambda: self._download_complete(model_name, button, False, str(e)))

        thread = threading.Thread(target=download_thread, daemon=True)
        thread.start()

    def _cancel_download(self):
        """Cancel the current download."""
        response = messagebox.askyesno(
            "Cancel Download",
            "Are you sure you want to cancel this download?\n\n"
            "Any partially downloaded files will be deleted."
        )

        if response:
            self.download_cancelled = True
            self.downloading = False

            # Update UI immediately
            self.progress_status.config(text="Cancelling download...")
            self.cancel_download_btn.config(state=tk.DISABLED, text="Cancelling...")
            self.dialog.update()

            # Try to delete partial downloads immediately
            if self.current_download_model:
                self._delete_partial_downloads(self.current_download_model)

            # Hide progress and re-enable UI
            self.progress_section.pack_forget()
            self.close_button.config(state=tk.NORMAL)

            messagebox.showinfo("Download Cancelled", "The download has been cancelled.")

    def _delete_partial_downloads(self, model_name: str):
        """Delete any partially downloaded model files."""
        try:
            if self.cache_dir.exists():
                # Look for partial downloads (might have .tmp, .part, or incomplete .pt files)
                import glob
                patterns = [
                    f'{model_name}*.pt',
                    f'{model_name}*.tmp',
                    f'{model_name}*.part',
                    f'{model_name}*.download'
                ]

                for pattern in patterns:
                    files = list(self.cache_dir.glob(pattern))
                    for file in files:
                        try:
                            file.unlink()
                            logging.info(f"Deleted partial download: {file}")
                        except:
                            pass
        except Exception as e:
            logging.error(f"Failed to delete partial downloads: {e}")

    def _cleanup_cancelled_download(self, model_name: str, button: tk.Button):
        """Clean up after a cancelled download that completed."""
        # Delete the downloaded model since user cancelled
        self._delete_partial_downloads(model_name)
        button.config(text="Download", state=tk.NORMAL, bg=config.WAVEFORM_SECONDARY_COLOR)

    def _download_complete(self, model_name: str, button: tk.Button, success: bool, error: str = None):
        """Handle download completion.

        Args:
            model_name: Name of the downloaded model.
            button: The download button.
            success: Whether download was successful.
            error: Error message if failed.
        """
        self.downloading = False

        # Hide progress section
        self.progress_section.pack_forget()

        if success:
            messagebox.showinfo("Download Complete",
                              f"The '{model_name}' model has been downloaded successfully!")
            # Refresh dialog to show delete button
            self.dialog.destroy()
            self.show()
        else:
            button.config(text="Download Failed", state=tk.NORMAL, bg=config.WAVEFORM_SECONDARY_COLOR)
            messagebox.showerror("Download Failed",
                               f"Failed to download the '{model_name}' model.\n\nError: {error}")

    def _delete_model(self, model_name: str, row_frame: tk.Frame):
        """Delete a downloaded Whisper model.

        Args:
            model_name: Name of the model to delete.
            row_frame: The row frame to refresh after deletion.
        """
        model_info = self.MODELS[model_name]

        # Check if this is the currently selected model
        if self.selected_model.get() == model_name:
            messagebox.showwarning(
                "Cannot Delete",
                f"Cannot delete the '{model_name}' model because it is currently selected.\n\n"
                f"Please select a different model first."
            )
            return

        # Confirm deletion
        response = messagebox.askyesno(
            "Confirm Deletion",
            f"Delete the '{model_name}' model?\n\n"
            f"Size: {model_info['size']}\n\n"
            f"This will free up disk space, but you'll need to download it again if you want to use it later."
        )

        if not response:
            return

        try:
            # Find and delete model files
            deleted_files = []
            if self.cache_dir.exists():
                model_files = list(self.cache_dir.glob(f'{model_name}*.pt'))
                for model_file in model_files:
                    model_file.unlink()
                    deleted_files.append(model_file.name)

            if deleted_files:
                messagebox.showinfo(
                    "Model Deleted",
                    f"The '{model_name}' model has been deleted successfully.\n\n"
                    f"Freed up approximately {model_info['size']} of disk space."
                )
                # Refresh the dialog to update UI
                self.dialog.destroy()
                self.show()
            else:
                messagebox.showwarning(
                    "No Files Found",
                    f"No files found for the '{model_name}' model in the cache directory."
                )

        except Exception as e:
            messagebox.showerror(
                "Delete Failed",
                f"Failed to delete the '{model_name}' model.\n\nError: {e}"
            )

    def _on_model_selected(self, *args):
        """Handle model selection change via radio button."""
        # Don't process during initialization
        if not self.initialized:
            return

        selected = self.selected_model.get()

        # Check if model is downloaded
        if not self._is_model_downloaded(selected):
            messagebox.showwarning(
                "Model Not Downloaded",
                f"The '{selected}' model has not been downloaded yet.\n\n"
                f"Please download it first using the 'Download' button."
            )
            # Revert selection
            self.selected_model.set(self.current_model)
            return

        # Only apply if different from current
        if selected == self.current_model:
            return

        # Save to config
        config.DEFAULT_WHISPER_MODEL = selected

        # Save to settings
        settings = settings_manager.load_all_settings()
        settings['whisper_model'] = selected
        settings_manager.save_all_settings(settings)

        # Reload the model in the backend if available
        if self.backend:
            try:
                self.backend.reload_model(selected)
                messagebox.showinfo(
                    "Model Changed",
                    f"Now using the '{selected}' model for transcriptions."
                )
            except Exception as e:
                messagebox.showerror(
                    "Error",
                    f"Failed to load the '{selected}' model: {e}"
                )
                # Revert on error
                self.selected_model.set(self.current_model)
                return
        else:
            messagebox.showinfo(
                "Model Changed",
                f"The '{selected}' model will be used for future transcriptions."
            )

        # Update current model
        self.current_model = selected

    def _close(self):
        """Close dialog."""
        if self.downloading:
            response = messagebox.askyesno(
                "Download in Progress",
                "A download is currently in progress.\n\n"
                "Do you want to cancel it?"
            )
            if response:
                # Cancel the download
                self.download_cancelled = True
                self.downloading = False
                if self.current_download_model:
                    self._delete_partial_downloads(self.current_download_model)
            else:
                # Don't close if they don't want to cancel
                return

        self.dialog.destroy()


if __name__ == "__main__":
    # Test the dialog
    dialog = WhisperModelDialog()
    dialog.show()
