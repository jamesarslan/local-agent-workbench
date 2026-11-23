---
name: workbench-gameplay-debug
description: "Debug gameplay that runs but behaves wrong: movement, physics, timers, input or state. Use to reproduce in a minimal scene, log state at the failing frame, check frame-rate and input timing."
license: MIT
compatibility: Engine examples are given for Godot 4, Unity and Unreal Engine 5.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- The game runs, but something behaves wrong: movement, jumping, collisions, timers, input, a state that never changes.
- A bug shows up only sometimes, only on one machine, or only at some frame rates.

Skip this skill for a compile error or an error message that names the file and line. Fix that directly.

## Steps

1. Write the bug as one sentence: `In <scene>, when <action>, I expect <X> but get <Y>.` If a part is missing, ask for it. Then name the two to four values that would show the bug in a log, such as position, velocity, a state name or a timer. If the bug can only be seen (an animation, particles, how the screen looks), say now that a person has to confirm the result.
2. Reproduce it in the smallest setup. Use the first that works:
   1. A test that builds the situation in code: create only the objects involved, set their start values, advance a fixed number of frames, read the values. The project's engine skill has the command that runs tests.
   2. The smallest existing scene or level that shows the bug, run with no window.
   3. Exact steps and a log from the user.

   Do not write a scene, level or prefab file by hand to get a test scene. If the bug does not appear, change no code; report what you ran.
3. Log the state at the failing frame. Add temporary lines marked `DEBUG-TMP` that print the frame number, the time step and the values from step 1:

   | Engine | Runs every drawn frame | Runs at a fixed step | Log line |
   | --- | --- | --- | --- |
   | Godot | `_process(delta)` | `_physics_process(delta)`, 60 per second by default | `print("DEBUG-TMP ", Engine.get_physics_frames(), " ", delta, " ", velocity)` |
   | Unity | `Update()` with `Time.deltaTime` | `FixedUpdate()`, 50 per second by default | `Debug.Log($"DEBUG-TMP {Time.frameCount} {Time.deltaTime} {transform.position}");` |
   | Unreal Engine | `Tick(float DeltaTime)` | | `UE_LOG(LogTemp, Warning, TEXT("DEBUG-TMP %llu %f %s"), GFrameCounter, DeltaTime, *GetActorLocation().ToString());` |

   Put the line inside a condition so it prints only around the event. Run the reproduction and find the first frame where a value is wrong. The cause is in code that ran on that frame or the one before it.
4. Test for frame-rate dependence. Run the same reproduction at 30 and at 120 frames per second and compare the values at the same game time, not at the same frame number.

   | Engine | How to force the frame rate |
   | --- | --- |
   | Godot | Add `--fixed-fps 30`, then `--fixed-fps 120`, to the run command. With `--quit-after`, ten seconds are 300 and 1200 frames. |
   | Unity | In the test or a start-up script: `QualitySettings.vSyncCount = 0; Application.targetFrameRate = 30;` then `120`. The target is ignored while `vSyncCount` is not 0. |
   | Unreal Engine | Add `-UseFixedTimeStep -FPS=30`, then `-FPS=120`, to the editor or game command line. |

   If the values differ, the code depends on frame rate. Look for these, in this order:
   - A value changed once per frame without the time step: `x += 5` must be `x += speed * delta`.
   - The time step applied twice, or applied to something that already uses it. In Godot, `move_and_slide()` uses the physics step itself, so `velocity` is in units per second and is not multiplied by `delta`.
   - Forces or body movement done in the every-frame function. They belong in the fixed-step function.
   - Smoothing with a constant factor each frame, such as `lerp(a, b, 0.1)`.
   - A timer that counts frames instead of seconds.
   - A fast object that passes through a thin collider: the distance moved in one step is larger than the collider.

   If the values are the same at both rates, go on to step 5.
5. Test input timing. Log the frame on which the input arrived and the frame on which the game acted on it. A gap, a miss or a double action shows the bug.
   - A "pressed this frame" question is true for one frame only.
   - Unity: read `Input.GetKeyDown` and `Input.GetButtonDown` in `Update`, keep the answer in a field, use it in `FixedUpdate` and clear it there. `FixedUpdate` runs zero, one or several times per drawn frame, so reading there misses presses.
   - Godot: `Input.is_action_just_pressed()` is true for one frame or one physics tick. For a one-time action, handle `event.is_action_pressed("<action>")` in `_unhandled_input(event)`. `grep -n -A 40 "^\[input\]" project.godot` lists the action names; check the spelling.
6. Test order and state.
   - Log every state change as `DEBUG-TMP <frame> <old> -> <new>`. Look for two changes in one frame, and for a state that is entered but never left.
   - Godot: the `_ready()` of child nodes runs before the `_ready()` of their parent.
   - Unity: every `Awake` runs before any `Start`, but the order of `Awake` between objects is not fixed. Set up your own fields in `Awake`; read other objects in `Start`.
   - In any engine, do not rely on which of two unrelated objects starts first.
7. Write one hypothesis: "It fails because <cause>. If so, the log will show <what> on frame <n>." Check it against the log before you change code. Then make one change and run the reproduction at both frame rates.
   - Fixed at both: go to step 8.
   - Not fixed: undo the change. After two failed fixes, stop and report what you tried.
8. Clean up. Remove every temporary line; `git grep -n --untracked "DEBUG-TMP"` must print nothing. Remove forced frame rates. If the project has tests, keep the reproduction from step 2 as a test that fails without the fix.

## Report

```
Bug: In <scene>, when <action>, expected <X>, got <Y>
Reproduce: <command or steps>
First wrong frame: <frame>: <value> (expected <value>)
Frame-rate dependent: yes | no (values at 30 and at 120 per second, same game time)
Cause: <one sentence with file:line>
Fix: <what you changed>
Proof: <command>: <result at 30>, <result at 120>
A person must check: <anything only visible on screen, or "nothing">
```

## Gotchas

- A run with no window draws nothing and plays no sound. It can prove numbers and states, not how the game looks or feels.
- Never compare two decimal numbers with `==`. Use `is_equal_approx(a, b)` in Godot, `Mathf.Approximately(a, b)` in Unity, `FMath::IsNearlyEqual(A, B)` in Unreal Engine.
- If the game uses random numbers, fix the seed while you debug: `seed(1234)` in Godot, `Random.InitState(1234)` in Unity, `FMath::RandInit(1234)` in Unreal Engine. Remove it afterwards.
- Slow motion helps a person see a fast bug: `--time-scale 0.25` in Godot, `Time.timeScale = 0.25f` in Unity.
- Godot can draw collision shapes with `--debug-collisions`, but only in a window, so a person has to look.
- In Unreal Engine, the console commands `stat fps` and `stat unit` show the frame rate and where frame time goes.
- One run that looks right is not proof for a bug that appears only sometimes. Run the reproduction five times and report every result.
