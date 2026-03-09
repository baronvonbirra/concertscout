import os
import shutil

def generate():
    dist_dir = "dist"
    if os.path.exists(dist_dir):
        shutil.rmtree(dist_dir)
    os.makedirs(dist_dir)

    with open("app.py", "r") as f:
        app_code = f.read()

    # Define minimal requirements for the app.py to run in stlite
    # Note: 'streamlit' is built into stlite. 'requests' is not needed by app.py.
    requirements = ['supabase==2.4.5', 'python-dotenv', 'pandas']

    # IMPORTANT: GitHub Pages is a public hosting service.
    # The following credentials will be visible to any user who visits the site.
    # Ensure that your Supabase instance has Row Level Security (RLS) enabled
    # and that you are using the "anon" key with limited permissions.
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")

    env_content = f"SUPABASE_URL={supabase_url}\nSUPABASE_KEY={supabase_key}\n"

    index_html = f"""
<!DOCTYPE html>
<html>
  <head>
    <meta charset="UTF-8" />
    <meta http-equiv="X-UA-Compatible" content="IE=edge" />
    <meta
      name="viewport"
      content="width=device-width, initial-scale=1, shrink-to-fit=no"
    />
    <title>PUNK-SCOUT V1.0</title>
    <link
      rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/@stlite/mountable@0.63.1/build/stlite.css"
    />
  </head>
  <body>
    <div id="root"></div>
    <script src="https://cdn.jsdelivr.net/npm/@stlite/mountable@0.63.1/build/stlite.js"></script>
    <script>
      stlite.mount(
        {{
          requirements: {requirements},
          entrypoint: "app.py",
          files: {{
            "app.py": {repr(app_code)},
            ".env": {repr(env_content)}
          }},
        }},
        document.getElementById("root")
      );
    </script>
  </body>
</html>
"""
    with open(os.path.join(dist_dir, "index.html"), "w") as f:
        f.write(index_html)

    print(f"Static site generated in {{dist_dir}} directory.")

if __name__ == "__main__":
    generate()
