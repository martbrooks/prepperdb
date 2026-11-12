"""Verify the constraint upgrade against an existing database with stock."""

import os
import subprocess
import tempfile
from pathlib import Path
import sqlite3
from io import BytesIO
import hashlib
from PIL import Image


def main():
    with tempfile.TemporaryDirectory(prefix="prepperdb-migrations-") as state:
        env = {**os.environ, "PREPPERDB_DEVELOPMENT": "1", "PREPPERDB_STATE_DIR": state}

        def manage(*args):
            subprocess.run([os.sys.executable, "manage.py", *args], env=env, check=True)

        manage("migrate", "stock", "0003", "--noinput")
        manage(
            "shell",
            "-c",
            "from stock.models import User; User.objects.create_user('migration-fixture')",
        )
        database = Path(state) / "stock.sqlite3"
        picture = BytesIO()
        Image.new("RGB", (16, 16), "blue").save(picture, "JPEG")
        image_bytes = picture.getvalue()
        digest = hashlib.sha256(image_bytes).hexdigest()
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO stock_productimage(digest,data,source_url,attribution,licence) VALUES(?,?,?,?,?)",
                (digest, image_bytes, "", "Migration fixture", ""),
            )
            connection.execute(
                "INSERT INTO stock_product(family,name,size_value,size_unit,packaging,category,revision,source_url,image_id) VALUES('11111111111111111111111111111111','Migration beans',NULL,'','','Food & drink / Tinned products',1,'https://world.openfoodfacts.org/product/5449000000996',NULL)"
            )
            connection.execute("UPDATE stock_product SET image_id=? WHERE id=1", (digest,))
            connection.execute(
                "INSERT INTO stock_batch(product_id,date_type,date,precision,quantity,revision) VALUES(1,'best_before','2028-02-29','month',6,1)"
            )
            connection.execute(
                "INSERT INTO stock_barcode(code,product_id,items_per_pack) VALUES('migration-code',1,6)"
            )
            connection.execute(
                "INSERT INTO stock_stockchange(batch_id,user_id,reason,before,after,revision,undone,created) VALUES(1,1,'add',0,6,1,0,datetime('now'))"
            )
        checkpoint = Path(state) / "checkpoint.sqlite3"
        with sqlite3.connect(database) as original, sqlite3.connect(checkpoint) as backup:
            original.backup(backup)
        manage("migrate", "--noinput")
        manage(
            "shell",
            "-c",
            "from stock.models import User,Batch,Barcode,StockChange; c=StockChange.objects.get(); assert c.product_name=='Migration beans' and c.batch_label=='Best before 02/2028' and (c.before,c.after)==(0,6); b=Batch.objects.select_related('product').get(); assert b.quantity==6 and b.date=='2028-02-29' and b.precision=='month' and b.product.name=='Migration beans' and b.product.image_id is not None; assert User.objects.filter(username='migration-fixture').exists(); assert Barcode.objects.get().items_per_pack==6; assert b.product.category.automatic_key=='tinned'; assert str(b.product.category)=='Food / Tinned Food'",
        )
        with sqlite3.connect(database) as upgraded:
            assert (
                upgraded.execute(
                    "SELECT data FROM stock_productimage WHERE digest=?", (digest,)
                ).fetchone()[0]
                == image_bytes
            )
            assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
        with sqlite3.connect(checkpoint) as restored:
            assert restored.execute(
                "SELECT quantity,date,precision FROM stock_batch"
            ).fetchone() == (6, "2028-02-29", "month")
            assert restored.execute("PRAGMA quick_check").fetchone() == ("ok",)
        print(
            "PASS: consistent pre-upgrade checkpoint retained; category-tree migration preserved existing user, stock, date precision and barcode pack count."
        )


if __name__ == "__main__":
    main()
