-- ~/.config/nvim/lua/plugins/drake_cursor.lua
--
-- Writes the word under the cursor (in Python files) to
--   $XDG_CACHE_HOME/drake_diagram_viewer/nvim_cursor.json    (default: ~/.cache/drake_diagram_viewer/)
-- so the Drake diagram viewer can select the block with that name.
-- Toggle with :DrakeCursorToggle

local uv = vim.uv or vim.loop

local cache_dir = (vim.env.XDG_CACHE_HOME or (vim.env.HOME .. "/.cache")) .. "/drake_diagram_viewer"
local out_path = cache_dir .. "/nvim_cursor.json"
local tmp_path = out_path .. ".tmp"

local enabled = true
local last_key = ""
local timer = uv.new_timer()

local function write_cursor()
  if not enabled then return end
  local word = vim.fn.expand "<cword>"
  if word == "" then return end -- cursor on whitespace / punctuation: keep the previous selection

  local file = vim.api.nvim_buf_get_name(0)
  local key = word .. "\0" .. file
  if key == last_key then return end -- same word as before: nothing to tell the viewer
  last_key = key

  local pos = vim.api.nvim_win_get_cursor(0)
  local json = vim.json.encode {
    word = word,
    file = file,
    line = pos[1],
    col = pos[2] + 1,
    time = os.time(),
  }

  vim.fn.mkdir(cache_dir, "p")
  local f = io.open(tmp_path, "w")
  if not f then return end
  f:write(json)
  f:close()
  uv.fs_rename(tmp_path, out_path) -- atomic: the viewer never reads a half-written file
end

-- wait until the cursor has rested for a moment, so fast scrolling does not spam the disk
local function schedule_write()
  timer:stop()
  timer:start(60, 0, vim.schedule_wrap(write_cursor))
end

---@type LazySpec
return {
  "AstroNvim/astrocore",
  ---@type AstroCoreOpts
  opts = {
    autocmds = {
      drake_cursor = {
        {
          event = { "CursorMoved", "BufEnter", "WinEnter" },
          pattern = "*.py",
          desc = "Tell the Drake diagram viewer which name is under the cursor",
          callback = schedule_write,
        },
      },
    },
    commands = {
      DrakeCursorToggle = {
        function()
          enabled = not enabled
          last_key = "" -- send the current word again when switched back on
          vim.notify("Drake cursor link " .. (enabled and "on" or "off"))
          if enabled then schedule_write() end
        end,
        desc = "Toggle writing the word under the cursor for the Drake diagram viewer",
      },
    },
  },
}
