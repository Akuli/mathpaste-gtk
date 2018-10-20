#!/usr/bin/env python3
import json
import os
import sys
import traceback

import appdirs
import gi
import lzstring

gi.require_version('Gtk', '3.0')        # noqa
gi.require_version('WebKit2', '4.0')    # noqa
from gi.repository import Gio, Gtk, WebKit2


MATHPASTE_URL = 'https://akuli.github.io/mathpaste/'
SETTINGS_JSON = os.path.join(
    appdirs.user_config_dir('mathpaste-gtk'), 'settings.json')

# https://python-gtk-3-tutorial.readthedocs.io/en/latest/application.html
MENU_XML = """
<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <menu id="app-menu">
    <section>
      <item>
        <attribute name="action">app.open</attribute>
        <attribute name="label" translatable="yes">_Open</attribute>
        <attribute name="accel">&lt;Primary&gt;o</attribute>
      </item>
      <item>
        <attribute name="action">app.save</attribute>
        <attribute name="label" translatable="yes">Save</attribute>
        <attribute name="accel">&lt;Primary&gt;s</attribute>
      </item>
      <item>
        <attribute name="action">app.saveas</attribute>
        <attribute name="label" translatable="yes">Save As</attribute>
        <attribute name="accel">&lt;Primary&gt;&lt;Shift&gt;s</attribute>
      </item>
    </section>
    <section>
      <item>
        <attribute name="action">win.zoomin</attribute>
        <attribute name="label" translatable="yes">Zoom In</attribute>
        <attribute name="accel">&lt;Primary&gt;plus</attribute>
      </item>
      <item>
        <attribute name="action">win.zoomout</attribute>
        <attribute name="label" translatable="yes">Zoom Out</attribute>
        <attribute name="accel">&lt;Primary&gt;minus</attribute>
      </item>
      <item>
        <attribute name="action">win.zoomreset</attribute>
        <attribute name="label" translatable="yes">Reset Zoom</attribute>
        <attribute name="accel">&lt;Primary&gt;0</attribute>
      </item>
    </section>
    <section>
      <item>
        <attribute name="action">app.quit</attribute>
        <attribute name="label" translatable="yes">_Quit</attribute>
        <attribute name="accel">&lt;Primary&gt;q</attribute>
      </item>
    </section>
  </menu>
</interface>
"""


class MathpasteWindow(Gtk.ApplicationWindow):

    def __init__(self, app, **kwargs):
        super().__init__(application=app, **kwargs)
        self.app = app

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(box)

        self.webview = WebKit2.WebView()
        self.webview.load_uri(MATHPASTE_URL)
        box.pack_start(self.webview, True, True, 0)

        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.pack_start(bottom_bar, False, False, 0)

        bottom_bar.add(Gtk.Label("Zoom %: "))
        self.zoom_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 10, 300, 10)
        self.zoom_scale.props.width_request = 200
        bottom_bar.add(self.zoom_scale)

        # for rolling mouse wheel on the slider
        self.zoom_scale.get_adjustment().set_page_increment(10)

        self.zoom_scale.set_value(app.config_dict['zoom'])
        self.webview.set_zoom_level(app.config_dict['zoom'] / 100)
        self.zoom_scale.connect('value-changed', self._zoom_scale2webview)
        self.webview.connect('notify::zoom-level', self._zoom_webview2scale)

        for how2zoom in ['in', 'out', 'reset']:
            action = Gio.SimpleAction.new('zoom' + how2zoom, None)
            action.connect('activate', getattr(self, '_zoom_' + how2zoom))
            self.add_action(action)

    def show_math(self, math):
        url_part = lzstring.LZString().compressToEncodedURIComponent(math)
        self.webview.load_uri(MATHPASTE_URL + '#fullmath:' + url_part)
        self.webview.reload()   # no idea why this is needed

    def get_showing_math(self):
        url = self.webview.get_uri()
        if url == MATHPASTE_URL:
            return ''

        assert url.startswith(MATHPASTE_URL + '#fullmath:')
        url_part = url[len(MATHPASTE_URL + '#fullmath:'):]
        return lzstring.LZString().decompressFromEncodedURIComponent(url_part)

    # these methods don't recurse infinitely for reasons that i can't explain
    def _zoom_webview2scale(self, webview, gparam):
        self.app.config_dict['zoom'] = round(webview.get_zoom_level() * 100)
        self.zoom_scale.set_value(self.app.config_dict['zoom'])

    def _zoom_scale2webview(self, scale):
        self.webview.set_zoom_level(scale.get_value() / 100)

    def _zoom_in(self, action, param):
        self.zoom_scale.set_value(self.zoom_scale.get_value() + 10)

    def _zoom_out(self, action, param):
        self.zoom_scale.set_value(self.zoom_scale.get_value() - 10)

    def _zoom_reset(self, action, param):
        self.zoom_scale.set_value(100)


class MathpasteApplication(Gtk.Application):

    def __init__(self):
        super().__init__(flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.config_dict = {
            'zoom': 100,
        }
        self.window = None
        self.current_filename = None

    # use this instead of setting self.current_filename when you want the
    # window title to update
    def set_current_filename(self, filename):
        self.current_filename = filename
        self.window.set_title("%s \N{em dash} MathPaste GTK" % filename)

    def do_open(self, giofiles, *junk):
        if len(giofiles) != 1:
            print("%s: can only open exactly 1 file at a time, not %d"
                  % (sys.argv[0], len(giofiles)), file=sys.stderr)
            sys.exit(2)

        # seems like do_open() always needs to do this, otherwise the app exits
        # without doing anything
        self.activate()

        # i didn't feel like figuring out how to read the file with gio, this
        # works fine
        self.open_file(giofiles[0].get_path())

    def do_startup(self):
        Gtk.Application.do_startup(self)    # no idea why super doesn't work

        for name in ['open', 'save', 'saveas', 'quit']:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', getattr(self, 'on_' + name))
            self.add_action(action)

        builder = Gtk.Builder.new_from_string(MENU_XML, -1)
        self.set_app_menu(builder.get_object("app-menu"))

    def do_activate(self):
        if self.window is None:
            self.window = MathpasteWindow(self, title="MathPaste GTK")
            self.window.set_default_size(800, 600)
        self.window.show_all()
        self.window.present()

    def _create_dialog(self, title, action, ok_stock):
        dialog = Gtk.FileChooserDialog(
            title, self.window, action,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
             ok_stock, Gtk.ResponseType.OK))

        filter = Gtk.FileFilter()
        filter.set_name("Text files")
        filter.add_mime_type("text/plain")
        dialog.add_filter(filter)

        filter = Gtk.FileFilter()
        filter.set_name("All files")
        filter.add_pattern("*")
        dialog.add_filter(filter)

        return dialog

    def open_file(self, path):
        with open(path, 'r', encoding='utf-8') as file:
            self.window.show_math(file.read().rstrip('\n'))

        self.set_current_filename(path)     # runs only if reading succeeded

    def on_open(self, action, param):
        dialog = self._create_dialog("Open Math", Gtk.FileChooserAction.OPEN,
                                     Gtk.STOCK_OPEN)
        if self.current_filename is not None:
            dialog.set_filename(self.current_filename)

        if dialog.run() == Gtk.ResponseType.OK:
            # TODO: error handling?
            self.open_file(dialog.get_filename())

        dialog.destroy()

    def on_save(self, action, param):
        if self.current_filename is None:
            return self.on_saveas(action, param)

        with open(self.current_filename, 'w', encoding='utf-8') as file:
            file.write(self.window.get_showing_math() + '\n')

    def on_saveas(self, action, param):
        dialog = self._create_dialog("Save Math", Gtk.FileChooserAction.SAVE,
                                     Gtk.STOCK_SAVE)
        if self.current_filename is None:
            dialog.set_current_name("math.txt")
        else:
            dialog.set_filename(self.current_filename)

        if dialog.run() == Gtk.ResponseType.OK:
            self.set_current_filename(dialog.get_filename())
            assert self.current_filename is not None
            self.on_save(action, param)

        dialog.destroy()

    def on_quit(self, action, param):
        self.quit()

    def read_config(self):
        with open(SETTINGS_JSON, 'r', encoding='utf-8') as file:
            self.config_dict.update(json.load(file))

    def write_config(self):
        os.makedirs(os.path.dirname(SETTINGS_JSON), exist_ok=True)
        with open(SETTINGS_JSON, 'w', encoding='utf-8') as file:
            json.dump(self.config_dict, file)


def main():
    app = MathpasteApplication()
    try:
        app.read_config()
    except FileNotFoundError:
        pass
    except Exception:
        traceback.print_exc()

    try:
        app.run(sys.argv)
    finally:
        app.write_config()


if __name__ == '__main__':
    main()
