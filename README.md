# s3-du

A `du` for Amazon S3: show how much storage each bucket and prefix uses, by storage class, without listing every object.

> **Project status: planning. There is no working code in this repository yet.**
> This README describes what the project will do. Nothing below can be installed or run today.
> Features are ticked off in the checklist as they are built.

## What it is

A command-line tool.

**The problem it solves:** Finding out what is using space in S3 normally means slow full listings or setting up inventory reports.

## Who it is for

Anyone trying to understand or reduce an S3 bill.

## How it will work

1. You name a bucket, or none to see them all.
2. The tool reads storage metrics first and lists objects only where you ask for more depth.
3. It prints sizes per bucket and prefix, largest first.

## Planned features

Unticked items are not built yet.

- [ ] Bucket sizes from CloudWatch storage metrics in seconds
- [ ] Per-prefix breakdown using parallel listing when you ask for it
- [ ] Split by storage class
- [ ] Incomplete multipart uploads and old versions reported separately
- [ ] Human-readable, JSON and CSV output

## Planned usage

This is the intended interface, shown so you can judge the design. **It does not work yet**, and names may change before the first release.

```bash
s3-du --bucket my-data --depth 2
```

## Contributing

Feedback on the design is the most useful contribution right now. Open an issue to suggest a feature, question an approach or describe your use case. Code contributions are welcome once the first version lands.

## Author

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
