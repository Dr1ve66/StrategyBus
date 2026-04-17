from app import create_app, db, ProductGuide

app = create_app()

with app.app_context():
    print("До загрузки:", ProductGuide.query.count())

    with open("/app/data/products.txt", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            parts = line.split(";")

            # пропускаем строку заголовка и кривые строки
            if parts[0] == "№":
                print(f"Строка {i} заголовок, пропускаем")
                continue
            if len(parts) < 4:
                print(f"Строка {i} пропущена: {line}")
                continue

            number = parts[0].strip()
            name = parts[1].strip()
            description = parts[2].strip()
            problems = parts[3].strip()

            p = ProductGuide(
                name=name,
                description=description,
                problems=problems
            )
            db.session.add(p)

    db.session.commit()

    print("После загрузки:", ProductGuide.query.count())

    first = ProductGuide.query.first()
    if first:
        print(first.id, first.name, first.description, first.problems)
    else:
        print("Записей всё ещё нет")
